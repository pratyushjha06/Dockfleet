import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Generator

import sqlalchemy
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine

logger = logging.getLogger(__name__)


class ContainerStatus(str, Enum):
    """
    Container lifecycle state enumeration.

    - RUNNING: Container is active and executing.
    - STOPPED: Container has exited cleanly or was stopped manually.
    - UNKNOWN: Container state cannot be determined or is missing from Docker daemon.
    """

    RUNNING = "running"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    """
    Health check status enumeration.

    - HEALTHY: Service passes health checks or is in a normal clean state.
    - UNHEALTHY: Service is failing health checks (external/API dimension).
    - CRASHED: Service failed consecutive health checks or exited unexpectedly.
    - RESTARTING: Service container restart is actively in progress.
    """

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    CRASHED = "crashed"
    RESTARTING = "restarting"


class SafeContainerStatusType(sqlalchemy.types.TypeDecorator):
    """Resilient Enum storage for ContainerStatus with safe fallback for corrupted legacy data."""

    impl = sqlalchemy.types.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Serialize ContainerStatus enum member or string to plain string for SQL storage."""
        if value is None:
            return ContainerStatus.UNKNOWN.value
        if isinstance(value, ContainerStatus):
            return value.value
        return str(value)

    def process_result_value(self, value, dialect):
        """Deserialize database string into ContainerStatus with warning logging on fallback."""
        if not value:
            # Empty or missing container status indicates undetermined state.
            logger.warning(
                "Empty or null ContainerStatus value %r in database, coercing to UNKNOWN",
                value,
            )
            return ContainerStatus.UNKNOWN
        try:
            return ContainerStatus(value)
        except (ValueError, KeyError):
            # Garbage or invalid container status string cannot be resolved to running/stopped.
            logger.warning(
                "Unrecognized ContainerStatus value %r in database, coercing to UNKNOWN",
                value,
            )
            return ContainerStatus.UNKNOWN


class SafeHealthStatusType(sqlalchemy.types.TypeDecorator):
    """Resilient Enum storage for HealthStatus with safe fallback for corrupted legacy data."""

    impl = sqlalchemy.types.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Serialize HealthStatus enum member or string to plain string for SQL storage."""
        if value is None:
            return HealthStatus.HEALTHY.value
        if isinstance(value, HealthStatus):
            return value.value
        return str(value)

    def process_result_value(self, value, dialect):
        """Deserialize database string into HealthStatus with warning logging on fallback."""
        if not value:
            # Uninitialized / legacy records created before health tracking default to HEALTHY.
            logger.warning(
                "Empty or null HealthStatus value %r in database, coercing to HEALTHY",
                value,
            )
            return HealthStatus.HEALTHY
        try:
            return HealthStatus(value)
        except (ValueError, KeyError):
            # Garbage / corrupted health string triggers defensive degradation to UNHEALTHY.
            logger.warning(
                "Unrecognized HealthStatus value %r in database, coercing to UNHEALTHY",
                value,
            )
            return HealthStatus.UNHEALTHY


class Service(SQLModel, table=True):
    """
    Service database table model representing a managed service and its runtime state.
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False, unique=True)
    image: str = Field(nullable=False)
    restart_policy: str = Field(nullable=False)
    # Serialized config fields
    ports_raw: str | None = Field(default=None)
    healthcheck_raw: str | None = Field(default=None)
    # Runtime state fields
    # status: container lifecycle (running / stopped / unknown)
    status: ContainerStatus = Field(
        default=ContainerStatus.UNKNOWN,
        sa_type=SafeContainerStatusType,
        nullable=False,
    )
    # health_status: health dimension (healthy / crashed / restarting / unhealthy)
    health_status: HealthStatus = Field(
        default=HealthStatus.HEALTHY,
        sa_type=SafeHealthStatusType,
        nullable=False,
    )
    restart_count: int = Field(default=0, nullable=False)
    last_health_check: datetime | None = Field(default=None)
    last_failure_reason: str | None = Field(default=None)
    consecutive_failures: int = Field(default=0, nullable=False)
    # Extra config for future dashboard / analytics
    # Example: resources.memory: "512m", resources.cpu: 0.5
    resources_memory: str | None = Field(default=None)
    resources_cpu: float | None = Field(default=None)
    # environment and depends_on stored in serialized form
    # env_raw: JSON string (e.g. '["DB_URL=postgres://...", "REDIS_URL=..."]')
    # depends_on_raw: comma-separated service names (e.g. "redis,db")
    env_raw: str | None = Field(default=None)
    depends_on_raw: str | None = Field(default=None)


class RestartEvent(SQLModel, table=True):
    """
    Historical restart event record for analytics and crash diagnostics.
    """

    id: int | None = Field(default=None, primary_key=True)
    service_id: int = Field(nullable=False, foreign_key="service.id")
    # Denormalized service name for easier analytics queries
    service_name: str = Field(nullable=False, index=True)
    restarted_at: datetime = Field(nullable=False, index=True)
    reason: str = Field(nullable=False)
    previous_status: str | None = Field(default=None)
    new_status: str | None = Field(default=None)


# LogEvent table model (log metadata skeleton)
# fields: id, service_id, service_name, created_at, level, message, source
class LogEvent(SQLModel, table=True):
    """
    Lightweight log metadata row for future log aggregation / crash analytics.
    Raw Docker logs will still be streamed separately; this table stores
    small, query-friendly summaries (who, when, what, where-from).
    """

    id: int | None = Field(default=None, primary_key=True)
    service_id: int = Field(nullable=False, foreign_key="service.id")
    # Index on service_name for fast per-service queries
    service_name: str = Field(nullable=False, index=True)
    # Index on created_at for time-ordered scans
    created_at: datetime = Field(nullable=False, index=True)
    # Optional metadata fields
    level: str | None = Field(default=None)  # e.g. "INFO", "WARN", "ERROR"
    message: str | None = Field(default=None)  # short summary / first line
    source: str | None = Field(
        default=None
    )  # e.g. "docker-logs", "scheduler", "orchestrator"


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "dockfleet.db"

_engines: dict[tuple[str, bool], Engine] = {}
_engines_lock = threading.Lock()


def get_engine(db_url: str | None = None, echo: bool = False) -> Engine:
    """
    Return a SQLAlchemy Engine instance lazily.

    Precedence order:
    1. Explicit `db_url` argument
    2. `DOCKFLEET_DB_URL` environment variable
    3. Default SQLite database at `PROJECT_ROOT / "dockfleet.db"`

    Features:
    - Thread-safe initialization via `_engines_lock`.
    - Cache key includes `(resolved_url, echo)` so different echo configurations are distinguished.
    - In-memory SQLite URLs (`sqlite://`, `sqlite:///:memory:`, or containing `:memory:`)
      automatically use `StaticPool` and `check_same_thread=False` to ensure all sessions
      share the exact same in-memory database without dropping tables between connections.
    """
    resolved_url = db_url or os.getenv("DOCKFLEET_DB_URL")
    if not resolved_url:
        resolved_url = f"sqlite:///{DB_PATH}"

    cache_key = (resolved_url, echo)
    with _engines_lock:
        if cache_key not in _engines:
            connect_args: dict[str, Any] = {}
            engine_kwargs: dict[str, Any] = {"echo": echo}

            if resolved_url.startswith("sqlite"):
                connect_args["timeout"] = 30
                if ":memory:" in resolved_url or resolved_url in ("sqlite://", "sqlite:///:memory:"):
                    connect_args["check_same_thread"] = False
                    engine_kwargs["poolclass"] = StaticPool

            engine_kwargs["connect_args"] = connect_args
            _engines[cache_key] = create_engine(
                resolved_url,
                **engine_kwargs,
            )
        return _engines[cache_key]


def reset_engine_cache() -> None:
    """Dispose of all cached engine connections and clear the cache thread-safely."""
    global _engines
    with _engines_lock:
        for eng in _engines.values():
            eng.dispose()
        _engines.clear()


@contextmanager
def get_session(
    db_url: str | None = None, engine: Engine | None = None
) -> Generator[Session, None, None]:
    """
    Context manager that provides a transactional SQLModel Session.

    Precedence:
    Explicit `engine` takes precedence over `db_url`, which in turn takes precedence
    over environment variable and default URL.
    """
    target_engine = engine or get_engine(db_url)
    with Session(target_engine) as session:
        yield session


def init_db(db_url: str | None = None, engine: Engine | None = None) -> None:
    """
    Initialize the SQLite database and create all missing tables.

    Precedence:
    Explicit `engine` takes precedence over `db_url`, which in turn takes precedence
    over environment variable and default URL.
    """
    target_engine = engine or get_engine(db_url)
    SQLModel.metadata.create_all(target_engine)


def __getattr__(name: str) -> Any:
    """Backward compatibility fallback for dynamic attribute access (e.g. engine)."""
    if name == "engine":
        return get_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


