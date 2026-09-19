from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from dockfleet.cli.config import load_config
from dockfleet.core.orchestrator import get_orchestrator
from dockfleet.dashboard.routes import router as dashboard_router
from dockfleet.health.log_ingestor import ingest_docker_logs_once
from dockfleet.health.models import PROJECT_ROOT, get_session, init_db
from dockfleet.health.models import Service as DBService
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.seed import bootstrap_from_path

# ✅ Create app FIRST
app = FastAPI()

# ✅ Mount static (correct place)
BASE_DIR = Path(__file__).resolve().parent
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

# ✅ Include routes (ONLY ONCE)
app.include_router(dashboard_router)

_health_scheduler: HealthScheduler | None = None


def _get_default_config_path() -> Path:
    """Return default YAML configuration path."""
    return Path("examples/dockfleet.yaml")


# ------------------------------------------------
# Startup
# ------------------------------------------------
@app.on_event("startup")
def on_startup() -> None:
    """Initialize database and bootstrap services on FastAPI application startup."""
    global _health_scheduler

    init_db()

    config_path = _get_default_config_path()

    try:
        bootstrap_from_path(str(config_path))
        print(f"Bootstrapped services from {config_path}")
    except Exception as exc:
        print("Bootstrap failed:", exc)

    try:
        config = load_config(config_path)
    except Exception as exc:
        print("Config load failed:", exc)
        return

    try:
        orch = get_orchestrator(config=config, self_healing=True)
        orch.up()
        print("Services started")
    except Exception as exc:
        print("Orchestrator failed:", exc)

    try:
        # Lock scope: PROJECT_ROOT (where dockfleet.db lives), not config dir.
        # This ensures CLI and dashboard always use the same lock regardless of
        # where the YAML config file is located.
        _health_scheduler = HealthScheduler(config, project_dir=PROJECT_ROOT)
        _health_scheduler.start()
        print("HealthScheduler started")
    except RuntimeError as exc:
        # Lock conflict: another scheduler is already running for this project.
        print(f"HealthScheduler skipped: {exc}")
    except Exception as exc:
        print("Scheduler failed:", exc)

    try:
        ingest_docker_logs_once(tail=200)
    except Exception as exc:
        print("Log ingestor failed:", exc)


# ------------------------------------------------
# Shutdown
# ------------------------------------------------
@app.on_event("shutdown")
def on_shutdown() -> None:
    global _health_scheduler

    if _health_scheduler:
        try:
            _health_scheduler.stop()
        except Exception as exc:
            print("Scheduler stop failed:", exc)


# ------------------------------------------------
# Fetch services (helper)
# ------------------------------------------------
def fetch_services() -> list[dict]:
    with get_session() as session:
        services = session.exec(select(DBService)).all()

        return [
            {
                "name": svc.name,
                "status": svc.status,
                "health_status": getattr(svc, "health_status", svc.status),
                "image": svc.image,
                "ports": svc.ports_raw,
                "restart_policy": svc.restart_policy,
                "restart_count": svc.restart_count,
                "last_health_check": getattr(svc, "last_health_check", None),
            }
            for svc in services
        ]
