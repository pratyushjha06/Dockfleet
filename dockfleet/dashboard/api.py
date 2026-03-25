from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlmodel import Session, select

from dockfleet.health.models import init_db, Service as DBService, engine
from dockfleet.dashboard.routes import router as dashboard_router
from dockfleet.health.log_ingestor import ingest_docker_logs_once
from dockfleet.cli.config import load_config
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.seed import bootstrap_from_path
from dockfleet.core.orchestrator import get_orchestrator

app = FastAPI()

_health_scheduler: HealthScheduler | None = None


def _get_default_config_path() -> Path:
    return Path("examples/dockfleet.yaml")


@app.on_event("startup")
def on_startup() -> None:
    global _health_scheduler

    # 1) Ensure DB schema exists
    init_db()

    # 2) Resolve config path and seed Service rows from YAML
    config_path = _get_default_config_path()
    try:
        bootstrap_from_path(str(config_path))
        print(f"Bootstrapped services from {config_path}")
    except Exception as exc:
        print("Bootstrap from config failed on startup:", exc)

    # 3) Load config
    try:
        config = load_config(config_path)
    except Exception as exc:
        print("Config load failed on startup:", exc)
        return

    # 4) Start services via Orchestrator.up() (non-blocking)
    try:
        orch = get_orchestrator(config=config, self_healing=True)
        orch.up()
        print("Orchestrator started services from dashboard startup")
    except Exception as exc:
        print("Failed to start services on startup:", exc)

    # 5) Start HealthScheduler
    try:
        _health_scheduler = HealthScheduler(config)
        _health_scheduler.start()
        print("HealthScheduler started from FastAPI dashboard")
    except Exception as exc:
        print("Failed to start HealthScheduler on startup:", exc)

    # 6) Warm log DB once (best-effort)
    try:
        ingest_docker_logs_once(tail=200)
    except Exception as exc:
        print("Log ingestor failed on startup:", exc)


@app.on_event("shutdown")
def on_shutdown() -> None:
    global _health_scheduler
    if _health_scheduler is not None:
        try:
            _health_scheduler.stop()
        except Exception as exc:
            print("HealthScheduler stop failed:", exc)
        _health_scheduler = None


def fetch_services() -> list[dict]:
    with Session(engine) as session:
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


# User-facing dashboard routes
app.include_router(dashboard_router)
