from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List
import time
import json

router = APIRouter()

templates = Jinja2Templates(directory="dockfleet/dashboard/templates")


class Service(BaseModel):
    name: str
    status: str
    image: str
    ports: str
    restart_policy: str
    restart_count: int


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
    return get_services()

def get_services() -> List[dict]:

    return [

        {
            "name": "api",
            "status": "running",
            "image": "dockfleet-api:latest",
            "ports": "8000:8000",
            "restart_policy": "always",
            "restart_count": 1
        },

        {
            "name": "worker",
            "status": "restarting",
            "image": "dockfleet-worker:latest",
            "ports": "-",
            "restart_policy": "on-failure",
            "restart_count": 5
        },

        {
            "name": "scheduler",
            "status": "stopped",
            "image": "dockfleet-scheduler:latest",
            "ports": "-",
            "restart_policy": "no",
            "restart_count": 2
        }

    ]


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

    services = get_services()

    total = len(services)
    running = sum(1 for s in services if s["status"] == "running")
    stopped = sum(1 for s in services if s["status"] == "stopped")

    return {
        "total_services": total,
        "running": running,
        "stopped": stopped
    }
    