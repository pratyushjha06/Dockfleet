import subprocess
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from dockfleet.dashboard.services import get_services
from dockfleet.core.logs import stream_container_logs
from dockfleet.health.status import (
    record_manual_restart_event,
    record_manual_stop,
)
from fastapi import Query
from sqlmodel import Session, select
from dockfleet.health.models import LogEvent, engine
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

router = APIRouter()

templates = Jinja2Templates(directory="dockfleet/dashboard/templates")


# ------------------------------------------------
# Basic health endpoint
# ------------------------------------------------
@router.get("/health")
def health_check():
    return {"status": "ok"}


# ------------------------------------------------
# Service schema (for documentation / typing)
# ------------------------------------------------
class Service(BaseModel):
    name: str
    status: str
    health_status: str
    image: str
    ports: str | None
    restart_policy: str
    restart_count: int
    last_health_check: Optional[datetime] = None

    cpu: Optional[str] = None
    memory: Optional[str] = None
    uptime: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_limit: Optional[str] = None

class ActionResponse(BaseModel):
    ok: bool
    message: str

# ------------------------------------------------
# Dashboard homepage
# ------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )


# ------------------------------------------------
# List services
# Combines DB state + Docker runtime stats
# ------------------------------------------------
@router.get("/services", response_model=List[Service])
def list_services():
    return get_services()

# ------------------------------------------------
# Restart service
# ------------------------------------------------
@router.post("/services/{name}/restart", response_model=ActionResponse)
def restart_service(name: str):

    container = f"dockfleet_{name}"

    result = subprocess.run(
        ["docker", "restart", container],
        capture_output=True
    )

    ok = result.returncode == 0

    if ok:
        # Update DB restart_count + insert RestartEvent
        record_manual_restart_event(name)

    return {
        "message": f"{name} restarted",
        "ok": ok
    }


# ------------------------------------------------
# Stop service
# ------------------------------------------------
@router.post("/services/{name}/stop", response_model=ActionResponse)
def stop_service(name: str):

    container = f"dockfleet_{name}"

    result = subprocess.run(
        ["docker", "stop", container],
        capture_output=True
    )

    ok = result.returncode == 0

    if ok:
        # Update DB status -> stopped
        record_manual_stop(name)

    return {
        "message": f"{name} stopped",
        "ok": ok
    }

@router.get("/logs/db")
def list_logs(
    service_name: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    """
    Return recent log events from DB.
    Optional filtering by service name or search text.
    """

    with Session(engine) as session:

        stmt = select(LogEvent)

        if service_name:
            stmt = stmt.where(LogEvent.service_name == service_name)

        if q:
            stmt = stmt.where(LogEvent.message.contains(q))

        stmt = stmt.order_by(LogEvent.created_at.desc()).limit(limit)

        rows = session.exec(stmt).all()

        return [
            {
                "id": log.id,
                "service_name": log.service_name,
                "timestamp": log.created_at,
                "level": log.level,
                "message": log.message,
                "source": log.source,
            }
            for log in rows
        ]

@router.get("/logs")
def get_logs(
    service_name: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100)
):
    from dockfleet.core.logs import get_logs_for_service

    # ❗ Prevent crash when no service selected
    if not service_name:
        return []

    logs = get_logs_for_service(service_name, limit)

    # 🔍 Search filter
    if q:
        logs = [log for log in logs if q.lower() in log.lower()]

    return logs

@router.get("/logs/download")
def download_logs(service_name: str = Query(None)):
    from dockfleet.core.logs import get_logs_for_service

    logs = get_logs_for_service(service_name, limit=500)

    content = "\n".join(logs)

    return PlainTextResponse(
        content,
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename={service_name or 'all'}_logs.txt"
        }
    )

# ------------------------------------------------
# System summary for dashboard
# ------------------------------------------------
@router.get("/status")
def system_status():

    services = get_services()

    total = len(services)

    running = sum(
        1 for s in services if s["health_status"] == "healthy"
    )

    restarting = sum(
        1 for s in services if s["health_status"] == "restarting"
    )

    unhealthy = sum(
        1 for s in services if s["health_status"] == "unhealthy"
    )

    stopped = sum(
        1 for s in services
        if s["health_status"] not in ["healthy", "restarting", "unhealthy"]
    )

    return {
        "total_services": total,
        "running": running,
        "restarting": restarting,
        "unhealthy": unhealthy,
        "stopped": stopped
    }


# ------------------------------------------------
# Stream container logs (SSE)
# ------------------------------------------------
@router.get("/logs/{service}")
async def stream_logs(service: str):

   

    async def event_stream():
        async for line in stream_container_logs(service):
            yield line

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
