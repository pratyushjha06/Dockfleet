from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import time

router = APIRouter()

# Setup templates
templates = Jinja2Templates(directory="dockfleet/dashboard/templates")


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/services")
def list_services():
    return [
        {"name": "api", "status": "running"},
        {"name": "worker", "status": "stopped"}
    ]


@router.get("/logs/{service}")
def stream_service_logs(service: str):
    def event_generator():
        counter = 1
        while True:
            log_line = f"[{service}] Log message {counter}"
            yield f"data: {log_line}\n\n"
            counter += 1
            time.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
    