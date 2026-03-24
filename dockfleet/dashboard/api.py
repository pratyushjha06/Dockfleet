from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlmodel import Session, select

from dockfleet.health.models import init_db, Service as DBService, engine
from dockfleet.dashboard.routes import router
from dockfleet.health.log_ingestor import ingest_docker_logs_once
from dockfleet.cli.config import load_config
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.seed import bootstrap_from_path

app = FastAPI()

_health_scheduler: HealthScheduler | None = None


@app.on_event("startup")
def on_startup() -> None:
    global _health_scheduler

    # 1) Ensure DB schema exists
    init_db()

    # 2) Seed Service rows from YAML (same as CLI)
    try:
        config_path = Path("examples/dockfleet.yaml")  # adjust if needed
        bootstrap_from_path(str(config_path))
    except Exception as exc:
        print("Bootstrap from config failed on startup:", exc)

    # 3) Warm log DB once (optional)
    try:
        ingest_docker_logs_once(tail=200)
    except Exception as exc:
        print("Log ingestor failed on startup:", exc)

    # 4) Load config and start HealthScheduler
    try:
        config = load_config(config_path)
        _health_scheduler = HealthScheduler(config)
        _health_scheduler.start()
        print("HealthScheduler started from FastAPI dashboard")
    except Exception as exc:
        print("Failed to start HealthScheduler on startup:", exc)


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


app.include_router(router)
