from __future__ import annotations
from fastapi import FastAPI
from sqlmodel import Session, select
from dockfleet.health.models import init_db, Service as DBService, engine
from dockfleet.dashboard.routes import router
from dockfleet.health.log_ingestor import ingest_docker_logs_once


app = FastAPI()

@app.on_event("startup")
def on_startup() -> None:
    # Ensure DB schema exists
    init_db()

    # Optional: warm log DB once so /logs/db has data for all services
    try:
        ingest_docker_logs_once(tail=200)
    except Exception as exc:
        # Avoid crashing startup if Docker not available
        print("Log ingestor failed on startup:", exc)


# (Optional helper, not used by FastAPI routes but can be handy for debugging)
def fetch_services() -> list[dict]:
    with Session(engine) as session:
        services = session.exec(select(DBService)).all()

        result: list[dict] = []
        for svc in services:
            result.append(
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
            )

        return result


# include dashboard routes
app.include_router(router)
