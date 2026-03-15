from sqlmodel import Session, select
from fastapi import FastAPI
from dockfleet.health.models import init_db
from dockfleet.health.models import Service as DBService, engine

app = FastAPI()

@app.on_event("startup")
def on_startup():
    init_db()
    
def fetch_services():

    with Session(engine) as session:

        services = session.exec(select(DBService)).all()

        result = []

        for svc in services:
            result.append(
                {
                    "name": svc.name,
                    "status": svc.status,
                    "health_status": svc.status,
                    "image": svc.image,
                    "ports": svc.ports_raw,
                    "restart_policy": svc.restart_policy,
                    "restart_count": svc.restart_count,
                    "last_health_check": svc.last_health_check,
                }
            )

        return result


# include dashboard routes
from dockfleet.dashboard.routes import router

app.include_router(router)
