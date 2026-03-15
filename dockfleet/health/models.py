from sqlmodel import Field, Session, SQLModel, create_engine
from datetime import datetime

"""
Service table model
fields: id, name, image, restart_policy, ports_raw, healthcheck_raw, status, restart_count, last_health_check, last_failure_reason, consecutive_failures
"""
class Service(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False, unique=True)
    image: str = Field(nullable=False)
    restart_policy: str = Field(nullable=False)
    ports_raw: str | None = Field(default=None)
    healthcheck_raw: str | None = Field(default=None)
    status: str = Field(default="unknown", nullable=False)
    restart_count: int = Field(default=0, nullable=False)
    last_health_check: datetime | None = Field(default=None)
    last_failure_reason: str | None = Field(default=None)
    consecutive_failures: int = Field(default=0, nullable=False)

# RestartEvent table model
# fields: id, service_id, restarted_at, reason, previous_status, new_status
class RestartEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    service_id: int = Field(nullable=False, foreign_key="service.id")
    restarted_at: datetime = Field(nullable=False)
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
    service_name: str = Field(nullable=False)

    created_at: datetime = Field(nullable=False)

    # Optional metadata fields
    level: str | None = Field(default=None)  # e.g. "INFO", "WARN", "ERROR"
    message: str | None = Field(default=None)  # short summary / first line
    source: str | None = Field(
        default=None
    )  # e.g. "docker-logs", "scheduler", "orchestrator"

# init_db() function
# work: engine + tables create
sqlite_file_name = "dockfleet.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url)

def init_db() -> None:
    SQLModel.metadata.create_all(engine)