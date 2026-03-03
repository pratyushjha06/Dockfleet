from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List
import time
import json

router = APIRouter()

# Setup templates
templates = Jinja2Templates(directory="dockfleet/dashboard/templates")

class Service(BaseModel):
    name: str
    status: str
    cpu: str
    memory: str
    uptime: str
    restart_count: int
    health_status: str



@router.get("/health")
def health_check():
    return {"status": "ok"}

@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

@router.get("/services", response_model=List[Service])
def list_services():
    services = [
        Service(
            name="api",
            status="running",
            cpu="12%",
            memory="256MB",
            uptime="2h 15m",
            restart_count=1,
            health_status="healthy"
        ),
        Service(
            name="worker",
            status="stopped",
            cpu="0%",
            memory="0MB",
            uptime="0h",
            restart_count=3,
            health_status="unhealthy"
        )
    ]

    return services


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
    