from fastapi import FastAPI
from dockfleet.dashboard.routes import router

app = FastAPI(
    title="DockFleet Dashboard API",
    version="0.1.0"
)

# Include routes
app.include_router(router)
