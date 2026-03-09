from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from dockfleet.dashboard.services import get_services_from_db_or_mock
from pydantic import BaseModel
from typing import List
from datetime import datetime
from typing import Optional
import time
import json

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
    Return service health information.
    Currently mocked for Day 10.
    """
    return get_services_from_db_or_mock()




@router.get("/logs/{service}")
def stream_service_logs(service: str):

    def event_generator():
        counter = 1
        while True:
            log_data = {
                "service": service,
                "message": f"Log entry {counter} from {service}",
                "level": "INFO"
            }

            yield f"data: {json.dumps(log_data)}\n\n"
            counter += 1
            time.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

@router.get("/status")
def system_status():

    services = get_services_from_db_or_mock()

    total = len(services)

    running = sum(
        1 for s in services if s["health_status"] == "healthy"
    )

    stopped = sum(
        1 for s in services if s["health_status"] != "healthy"
    )

    return {
        "total_services": total,
        "running": running,
        "stopped": stopped
    }

    