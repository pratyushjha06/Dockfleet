# DockFleet Dashboard Module

## Dashboard API Surface 

GET  /health
GET  /services
GET  /logs/{service}
GET  /metrics
GET  /

Purpose:
- /health     → API health check
- /services   → list of monitored services
- /logs/{service} → real-time logs (SSE later)
- /metrics    → aggregated metrics
- /           → dashboard UI

## Run Locally

uvicorn dockfleet.dashboard.api:app --reload --port 8080

### Dashboard

DockFleet provides a dashboard powered by FastAPI.

The dashboard fetches service information from the `/services` endpoint.

Fields returned by `/services`:

name – service name  
status – running / unhealthy / restarting / stopped  
health_status – latest health check result  
image – Docker image used by the service  
ports – exposed container ports  
restart_policy – restart policy of the service  
restart_count – number of automatic restarts  
last_health_check – timestamp of last health check

Future versions will also expose CPU and memory usage.

### Dashboard Controls

The dashboard allows basic container control directly from the UI.

Each service card includes:

- **Restart** – triggers `POST /services/{name}/restart`
- **Stop** – triggers `POST /services/{name}/stop`

The dashboard fetches service data from the `/services` endpoint.

Runtime fields such as **CPU usage**, **memory usage**, and **uptime** are retrieved from Docker using `docker stats`.  
These metrics are **local-only** and are not sent to any external services.