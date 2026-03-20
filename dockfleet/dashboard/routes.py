import subprocess
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Query, Request
from fastapi.responses import (
    HTMLResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from dockfleet.dashboard.services import get_services
from dockfleet.core.logs import stream_container_logs
from dockfleet.health.status import (
    record_manual_restart_event,
    record_manual_stop,
)
from dockfleet.health.logs import (
    query_logs,
    iter_logs_as_text,
    iter_logs_as_csv,
)
from dockfleet.health.queries import (
    get_most_unstable_services,
    get_restart_history,
    get_failure_reasons_breakdown,
)
from dockfleet.health.models import RestartEvent, engine
from sqlmodel import Session, select

router = APIRouter()

templates = Jinja2Templates(directory="dockfleet/dashboard/templates")


# ------------------------------------------------
# Basic health endpoint
# ------------------------------------------------
@router.get("/health")
def health_check():
    return {"status": "ok"}


# ------------------------------------------------
# Service schema
# ------------------------------------------------
class Service(BaseModel):
    """Runtime state of a single DockFleet-managed service."""

    name: str = Field(..., description="Service name as defined in dockfleet.yaml")
    status: str = Field(..., description="Raw container status from Docker")
    health_status: str = Field(..., description="DockFleet health state: healthy | unhealthy | restarting | stopped | unknown")
    image: str = Field(..., description="Docker image used by this service")
    ports: Optional[str] = Field(None, description="Port mappings, e.g. 8000:80")
    restart_policy: str = Field(..., description="Restart policy: always | on-failure | never")
    restart_count: int = Field(..., description="Total number of times this service has been restarted")
    last_health_check: Optional[datetime] = Field(None, description="UTC timestamp of the last health check")

    cpu: Optional[str] = Field(None, description="Current CPU usage percentage")
    memory: Optional[str] = Field(None, description="Current memory usage")
    uptime: Optional[str] = Field(None, description="How long the container has been running")
    cpu_limit: Optional[str] = Field(None, description="CPU limit defined in YAML resources")
    memory_limit: Optional[str] = Field(None, description="Memory limit defined in YAML resources")


class ActionResponse(BaseModel):
    """Response returned after a manual restart or stop action."""

    ok: bool = Field(..., description="True if the action succeeded")
    message: str = Field(..., description="Human-readable result message")


# ------------------------------------------------
# Crash Analytics Pydantic models
# ------------------------------------------------
class UnstableService(BaseModel):
    """
    A service that has restarted frequently within a given time window.
    Used in /analytics/unstable-services and /analytics/summary.
    """

    service_name: str = Field(..., description="Name of the service")
    restarts: int = Field(..., description="Number of restarts in the requested time window")
    last_restart_at: Optional[datetime] = Field(
        None, description="UTC timestamp of the most recent restart, if any"
    )


class RestartEventItem(BaseModel):
    """
    A single restart event for a service.
    Used in /analytics/restart-history/{service_name}.
    """

    timestamp: datetime = Field(..., description="UTC time when the restart occurred")
    reason: str = Field(..., description="Why the restart was triggered, e.g. 3_failed_health_checks, manual")
    previous_status: Optional[str] = Field(None, description="Health status before the restart")
    new_status: Optional[str] = Field(None, description="Health status after the restart")


class FailureReasonCount(BaseModel):
    """
    Aggregated count of a single failure reason for a service.
    Used in /analytics/failure-reasons/{service_name}.
    """

    reason: str = Field(..., description="Failure reason category, e.g. healthcheck_timeout, crash_loop, manual_restart, other")
    count: int = Field(..., description="Number of times this reason occurred in the time window")


class AnalyticsSummary(BaseModel):
    """
    Top-level crash analytics summary for the entire DockFleet stack.
    Returned by /analytics/summary.

    Gives a single snapshot of system stability: which services are
    most unstable, and overall restart and failure totals in the
    requested time window.
    """

    window_hours: int = Field(..., description="Time window used for aggregation, in hours")
    total_restarts: int = Field(..., description="Total restart events across all services in the window")
    total_health_failures: int = Field(..., description="Total health check failures that triggered restarts")
    most_unstable_services: List[UnstableService] = Field(
        ..., description="Top services ranked by restart count, most unstable first"
    )


# ------------------------------------------------
# Metrics Pydantic model
# ------------------------------------------------
class MetricsSummary(BaseModel):
    """
    System-level metrics snapshot for the entire DockFleet deployment.
    Returned by /metrics.

    Designed to be consumed by dashboards, alerting tools, or any
    external monitoring system that can poll a JSON HTTP endpoint.
    All counts reflect the state at collected_at.
    """

    total_services: int = Field(..., description="Total services registered in DockFleet")
    running_services: int = Field(..., description="Services currently in healthy state")
    unhealthy_services: int = Field(..., description="Services currently failing health checks")
    stopped_services: int = Field(..., description="Services that are stopped")
    total_restarts: int = Field(..., description="Cumulative restart count across all services (all time)")
    health_failures: int = Field(..., description="Restart events recorded in the last 24 hours (proxy for recent failures)")
    collected_at: datetime = Field(..., description="UTC timestamp when these metrics were collected")


# ------------------------------------------------
# Dashboard homepage
# ------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request},
    )


# ------------------------------------------------
# List services
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
    result = subprocess.run(["docker", "restart", container], capture_output=True)
    ok = result.returncode == 0
    if ok:
        record_manual_restart_event(name)
    return {"message": f"{name} restarted", "ok": ok}


# ------------------------------------------------
# Stop service
# ------------------------------------------------
@router.post("/services/{name}/stop", response_model=ActionResponse)
def stop_service(name: str):
    container = f"dockfleet_{name}"
    result = subprocess.run(["docker", "stop", container], capture_output=True)
    ok = result.returncode == 0
    if ok:
        record_manual_stop(name)
    return {"message": f"{name} stopped", "ok": ok}


# ------------------------------------------------
# DB-backed logs API
# ------------------------------------------------
@router.get("/logs/db")
def list_logs(
    service_name: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    events = query_logs(service_name=service_name, q=q, limit=limit, offset=offset)
    return [
        {
            "id": log.id,
            "service_name": log.service_name,
            "timestamp": log.created_at,
            "level": log.level,
            "message": log.message,
            "source": log.source,
        }
        for log in events
    ]


# ------------------------------------------------
# Legacy /logs: live docker logs
# ------------------------------------------------
@router.get("/logs")
def get_logs(
    service_name: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100),
):
    from dockfleet.core.logs import get_logs_for_service

    if not service_name:
        return []

    logs = get_logs_for_service(service_name, limit)
    if q:
        logs = [log for log in logs if q.lower() in log.lower()]
    return logs


# ------------------------------------------------
# Download logs
# ------------------------------------------------
@router.get("/logs/download")
def download_logs(
    service_name: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    format: str = Query("text", pattern="^(text|csv)$"),
):
    if format == "csv":
        return StreamingResponse(
            iter_logs_as_csv(service_name=service_name, q=q),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{service_name or "all"}_logs.csv"'},
        )
    return StreamingResponse(
        iter_logs_as_text(service_name=service_name, q=q),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{service_name or "all"}_logs.txt"'},
    )


# ------------------------------------------------
# System status summary
# ------------------------------------------------
@router.get("/status")
def system_status():
    services = get_services()
    total = len(services)
    running = sum(1 for s in services if s["health_status"] == "healthy")
    restarting = sum(1 for s in services if s["health_status"] == "restarting")
    unhealthy = sum(1 for s in services if s["health_status"] == "unhealthy")
    stopped = sum(
        1 for s in services
        if s["health_status"] not in ["healthy", "restarting", "unhealthy"]
    )
    return {
        "total_services": total,
        "running": running,
        "restarting": restarting,
        "unhealthy": unhealthy,
        "stopped": stopped,
    }


# ------------------------------------------------
# Stream container logs (SSE)
# ------------------------------------------------
@router.get("/logs/{service}")
async def stream_logs(service: str):
    async def event_stream():
        async for line in stream_container_logs(service):
            yield line

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ------------------------------------------------
# Metrics endpoint
# ------------------------------------------------
@router.get(
    "/metrics",
    response_model=MetricsSummary,
    summary="System-level metrics snapshot",
    description=(
        "Returns a real-time snapshot of DockFleet system health. "
        "Suitable for polling by external monitoring tools or dashboards. "
        "All values reflect the state at collected_at (UTC)."
    ),
)
def get_metrics():
    services = get_services()

    total = len(services)
    running = sum(1 for s in services if s["health_status"] == "healthy")
    unhealthy = sum(1 for s in services if s["health_status"] == "unhealthy")
    stopped = sum(
        1 for s in services
        if s["health_status"] not in ["healthy", "restarting", "unhealthy"]
    )
    total_restarts = sum(s.get("restart_count", 0) for s in services)

    since = datetime.utcnow() - timedelta(hours=24)
    with Session(engine) as session:
        stmt = select(RestartEvent).where(RestartEvent.restarted_at >= since)
        health_failures = len(session.exec(stmt).all())

    return MetricsSummary(
        total_services=total,
        running_services=running,
        unhealthy_services=unhealthy,
        stopped_services=stopped,
        total_restarts=total_restarts,
        health_failures=health_failures,
        collected_at=datetime.utcnow(),
    )


# ------------------------------------------------
# Analytics: top-level summary
# ------------------------------------------------
@router.get(
    "/analytics/summary",
    response_model=AnalyticsSummary,
    summary="Crash analytics summary",
    description="Overall stability snapshot — total restarts, failures, and top unstable services in the requested window.",
)
def analytics_summary(
    limit: int = Query(5, ge=1, le=20, description="Max number of unstable services to return"),
    window_hours: int = Query(24, ge=1, le=168, description="Look-back window in hours (max 168 = 7 days)"),
):
    since = datetime.utcnow() - timedelta(hours=window_hours)
    base = get_most_unstable_services(limit=limit, window_hours=window_hours)

    with Session(engine) as session:
        stmt_total = select(RestartEvent).where(RestartEvent.restarted_at >= since)
        total_restarts = len(session.exec(stmt_total).all())

        unstable: list[UnstableService] = []
        for row in base:
            name = row["service_name"]
            stmt_last = (
                select(RestartEvent)
                .where(RestartEvent.service_name == name)
                .order_by(RestartEvent.restarted_at.desc())
                .limit(1)
            )
            last = session.exec(stmt_last).one_or_none()
            unstable.append(
                UnstableService(
                    service_name=name,
                    restarts=row["restarts"],
                    last_restart_at=last.restarted_at if last else None,
                )
            )

    return AnalyticsSummary(
        window_hours=window_hours,
        total_restarts=total_restarts,
        total_health_failures=total_restarts,
        most_unstable_services=unstable,
    )


# ------------------------------------------------
# Analytics: most unstable services
# ------------------------------------------------
@router.get(
    "/analytics/unstable-services",
    response_model=List[UnstableService],
    summary="Most unstable services",
    description="Top N services ranked by restart count within the requested time window.",
)
def analytics_unstable_services(
    limit: int = Query(5, ge=1, le=20, description="Max number of services to return"),
    window_hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
):
    base = get_most_unstable_services(limit=limit, window_hours=window_hours)

    with Session(engine) as session:
        results: list[UnstableService] = []
        for row in base:
            name = row["service_name"]
            stmt = (
                select(RestartEvent)
                .where(RestartEvent.service_name == name)
                .order_by(RestartEvent.restarted_at.desc())
                .limit(1)
            )
            last = session.exec(stmt).one_or_none()
            results.append(
                UnstableService(
                    service_name=name,
                    restarts=row["restarts"],
                    last_restart_at=last.restarted_at if last else None,
                )
            )

    return results


# ------------------------------------------------
# Analytics: restart history for a service
# ------------------------------------------------
@router.get(
    "/analytics/restart-history/{service_name}",
    response_model=List[RestartEventItem],
    summary="Restart history for a service",
    description="Returns a list of restart events for the given service, most recent first.",
)
def analytics_restart_history(
    service_name: str,
    since_hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
):
    since = datetime.utcnow() - timedelta(hours=since_hours)
    history = get_restart_history(service_name, since=since)

    return [
        RestartEventItem(
            timestamp=item["timestamp"],
            reason=item["reason"],
            previous_status=item["previous_status"],
            new_status=item["new_status"],
        )
        for item in history
    ]


# ------------------------------------------------
# Analytics: failure reason breakdown
# ------------------------------------------------
@router.get(
    "/analytics/failure-reasons/{service_name}",
    response_model=List[FailureReasonCount],
    summary="Failure reason breakdown for a service",
    description=(
        "Returns aggregated restart reason counts for a service in the requested window. "
        "Reasons include: healthcheck_timeout, crash_loop, manual_restart, other."
    ),
)
def analytics_failure_reasons(
    service_name: str,
    window_hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
):
    breakdown = get_failure_reasons_breakdown(
        service_name=service_name,
        window_hours=window_hours,
    )

    items = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)

    return [
        FailureReasonCount(reason=reason, count=count)
        for reason, count in items
    ]
    