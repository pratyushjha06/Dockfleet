from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(
    title="DockFleet Dashboard API",
    version="0.1.0"
)

# Setup templates directory
templates = Jinja2Templates(directory="dockfleet/dashboard/templates")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/services")
def list_services():
    return [
        {"name": "api", "status": "running"},
        {"name": "worker", "status": "stopped"}
    ]

@app.get("/logs/{service}")
def get_service_logs(service: str):
    return {
        "service": service,
        "message": "logs stream will be available here"
    }
    
@app.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
    