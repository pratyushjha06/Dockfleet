import subprocess
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from dockfleet.dashboard.services import get_services
from dockfleet.core.logs import stream_container_logs
from pydantic import BaseModel
from typing import List
from datetime import datetime
from typing import Optional

router = APIRouter()

templates = Jinja2Templates(directory="dockfleet/dashboard/templates")


@router.get("/health")
def health_check():
    return {"status": "ok"}

class Service(BaseModel):
    name: str
    status: str
    health_status: str
    image: str
    ports: str | None
    restart_policy: str
    restart_count: int
    last_health_check: Optional[datetime] = None

@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

@router.get("/services")
def list_services():
    """
    Return services combining DB + Docker status.
    """
    return get_services()

@router.post("/services/{name}/restart")
def restart_service(name: str):

    container = f"dockfleet_{name}"

    subprocess.run(
        ["docker", "restart", container],
        capture_output=True
    )

    return {"message": f"{name} restarted"}

@router.post("/services/{name}/stop")
def stop_service(name: str):

    container = f"dockfleet_{name}"

    subprocess.run(
        ["docker", "stop", container],
        capture_output=True
    )

    return {"message": f"{name} stopped"}
    
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
        1 for s in services if s["health_status"] not in ["healthy", "restarting", "unhealthy"]
    )

    return {
        "total_services": total,
        "running": running,
        "restarting": restarting,
        "unhealthy": unhealthy,
        "stopped": stopped
    }

@router.get("/logs/{service}")
async def stream_logs(service: str):
    """
    Stream container logs to browser using Server Sent Events (SSE)
    """

    # For now service name = container name
    container_name = f"dockfleet_{service}"

    def event_stream():
        for line in stream_container_logs(container_name):
            yield f"data: {line}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream") 
    