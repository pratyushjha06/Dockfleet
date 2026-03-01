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