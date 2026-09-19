import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from dockfleet.core.logs import get_logs_services, stream_container_logs
from dockfleet.core.orchestrator import get_orchestrator
from dockfleet.dashboard.services import get_services
from dockfleet.health.logs import (
    iter_logs_as_csv,
    iter_logs_as_text,
    query_logs,
)
from dockfleet.health.models import (
    ContainerStatus,
    HealthStatus,
    LogEvent,
    RestartEvent,
    get_session,
)
from dockfleet.health.queries import (
    get_failure_reasons_breakdown,
    get_most_unstable_services,
    get_restart_history,
)
from dockfleet.health.status import (
    record_manual_restart_event,
    record_manual_stop,
)

router = APIRouter()
templates = Jinja2Templates(directory="dockfleet/dashboard/templates")

IST = timezone(timedelta(hours=5, minutes=30))


def to_ist_iso(dt: datetime | None) -> str | None:
    """Convert UTC datetime to IST ISO string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt_utc = dt.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt.astimezone(timezone.utc)
    dt_ist = dt_utc.astimezone(IST)
    return dt_ist.isoformat()


# ------------------------------------------------
# Basic health endpoint
# ------------------------------------------------
@router.get("/health")
def health_check():
    """Basic health check endpoint returning service status."""
    return {"status": "ok"}


# ------------------------------------------------
# Service schema
# ------------------------------------------------
class Service(BaseModel):
    """Runtime state of a single DockFleet-managed service."""

    name: str = Field(..., description="Service name as defined in dockfleet.yaml")
    status: str = Field(..., description="Raw container status from Docker")
    health_status: str = Field(
        ...,
        description=(
            "DockFleet health state: healthy | unhealthy | restarting | stopped | unknown"
        ),
    )
    image: str = Field(..., description="Docker image used by this service")
    ports: str | None = Field(None, description="Port mappings, e.g. 8000:80")
    restart_policy: str = Field(
        ..., description="Restart policy: always | on-failure | never"
    )
    restart_count: int = Field(
        ..., description="Total number of times this service has been restarted"
    )
    # Serialized as IST ISO string
    last_health_check: str | None = Field(
        None, description="IST timestamp of the last health check (ISO string)"
    )

    cpu: str | None = Field(None, description="Current CPU usage percentage")
    memory: str | None = Field(None, description="Current memory usage")
    uptime: str | None = Field(
        None, description="How long the container has been running"
    )
    cpu_limit: str | None = Field(
        None, description="CPU limit defined in YAML resources"
    )
    memory_limit: str | None = Field(
        None, description="Memory limit defined in YAML resources"
    )


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
    restarts: int = Field(
        ..., description="Number of restarts in the requested time window"
    )
    # IST ISO timestamp
    last_restart_at: str | None = Field(
        None,
        description="IST timestamp (ISO) of the most recent restart, if any",
    )


class RestartEventItem(BaseModel):
    """
    A single restart event for a service.
    Used in /analytics/restart-history/{service_name}.
    """

    # IST ISO timestamp
    timestamp: str = Field(..., description="IST time (ISO) when the restart occurred")
    reason: str = Field(
        ...,
        description=(
            "Why the restart was triggered, e.g. 3_failed_health_checks, manual"
        ),
    )
    previous_status: str | None = Field(
        None, description="Health status before the restart"
    )
    new_status: str | None = Field(None, description="Health status after the restart")


class FailureReasonCount(BaseModel):
    """
    Aggregated count of a single failure reason for a service.
    Used in /analytics/failure-reasons/{service_name}.
    """

    reason: str = Field(
        ...,
        description=(
            "Failure reason category, e.g. healthcheck_timeout, "
            "crash_loop, manual_restart, other"
        ),
    )
    count: int = Field(
        ..., description="Number of times this reason occurred in the time window"
    )


class AnalyticsSummary(BaseModel):
    """
    Top-level crash analytics summary for the entire DockFleet stack.
    Returned by /analytics/summary.
    """

    window_hours: int = Field(
        ..., description="Time window used for aggregation, in hours"
    )
    total_restarts: int = Field(
        ..., description="Total restart events across all services in the window"
    )
    total_health_failures: int = Field(
        ..., description="Total health check failures that triggered restarts"
    )
    most_unstable_services: list[UnstableService] = Field(
        ...,
        description="Top services ranked by restart count, most unstable first",
    )


# ------------------------------------------------
# Metrics Pydantic model
# ------------------------------------------------
class MetricsSummary(BaseModel):
    """
    System-level metrics snapshot for the entire DockFleet deployment.
    Returned by /metrics.
    """

    total_services: int = Field(
        ..., description="Total services registered in DockFleet"
    )
    running_services: int = Field(
        ..., description="Services currently in healthy state"
    )
    unhealthy_services: int = Field(
        ..., description="Services currently failing health checks"
    )
    stopped_services: int = Field(..., description="Services that are stopped")
    total_restarts: int = Field(
        ..., description="Cumulative restart count across all services (all time)"
    )
    health_failures: int = Field(
        ..., description="Restart events recorded in the last 24 hours"
    )
    collected_at: str = Field(
        ..., description="IST timestamp (ISO) when these metrics were collected"
    )


# ------------------------------------------------
# Dashboard homepage
# ------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request):
    """Render the dashboard HTML home page."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request},
    )


# ------------------------------------------------
# List services
# ------------------------------------------------
@router.get("/services", response_model=list[Service])
def list_services():
    """List all managed services and their current runtime/health status."""
    raw_services = get_services()

    converted: list[dict] = []
    for svc in raw_services:
        svc = dict(svc)
        if "last_health_check" in svc and isinstance(
            svc["last_health_check"], datetime
        ):
            svc["last_health_check"] = to_ist_iso(svc["last_health_check"])
        converted.append(svc)

    return converted


# ------------------------------------------------
# Restart service (manual)
# ------------------------------------------------
@router.post("/services/{name}/restart", response_model=ActionResponse)
def restart_service(name: str):
    """Trigger a manual container restart for the given service."""
    orch = get_orchestrator()
    ok = orch.restart_service(name)
    if ok:
        record_manual_restart_event(name)
        return {"message": f"{name} restarted", "ok": True}
    return {"message": f"Failed to restart {name}", "ok": False}


# ------------------------------------------------
# Stop service (manual)
# ------------------------------------------------
@router.post("/services/{name}/stop", response_model=ActionResponse)
def stop_service(name: str):
    """Trigger a manual container stop for the given service."""
    container = f"dockfleet_{name}"
    try:
        result = subprocess.run(["docker", "stop", container], capture_output=True)
        ok = result.returncode == 0
    except Exception:
        ok = False
    if ok:
        record_manual_stop(name)
        return {"message": f"{name} stopped", "ok": True}
    return {"message": f"Failed to stop {name}", "ok": False}


# ------------------------------------------------
# DB-backed logs API (history)
# ------------------------------------------------
@router.get("/logs/db")
def list_logs(
    service_name: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Query persisted structured logs from SQLite database."""
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
# Time-based log history
# ------------------------------------------------
@router.get("/logs/explore/{service_name}")
async def explore_logs(service_name: str, days: int = 1):
    """Retrieve time-windowed log records for a service."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with get_session() as session:
        statement = (
            select(LogEvent)
            .where(
                LogEvent.service_name == service_name,
                LogEvent.created_at > cutoff,
            )
            .order_by(LogEvent.created_at.desc())
            .limit(500)
        )
        logs = session.exec(statement).all()
        return [{"timestamp": log.created_at, "message": log.message} for log in logs]


# ------------------------------------------------
# Legacy /logs: live docker logs (non-DB)
# ------------------------------------------------
@router.get("/logs")
def get_logs(
    service_name: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100),
):
    """Fetch live tail logs from Docker container."""
    if not service_name:
        return []

    logs = get_logs_services(service_name, limit)
    if q:
        logs = [log for log in logs if q.lower() in log.lower()]
    return logs


# ------------------------------------------------
# Download logs
# ------------------------------------------------
@router.get("/logs/download")
def download_logs(
    service_name: str | None = Query(default=None),
    q: str | None = Query(default=None),
    format: str = Query("text", pattern="^(text|csv)$"),
):
    """Export logs as downloadable text or CSV file stream."""
    if format == "csv":
        return StreamingResponse(
            iter_logs_as_csv(service_name=service_name, q=q),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{service_name or "all"}_logs.csv"'
                )
            },
        )
    return StreamingResponse(
        iter_logs_as_text(service_name=service_name, q=q),
        media_type="text/plain",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{service_name or "all"}_logs.txt"'
            )
        },
    )


# ------------------------------------------------
# System status summary
# ------------------------------------------------
@router.get("/status")
def system_status():
    """Return aggregated count of running, restarting, unhealthy, and stopped services."""
    services = get_services()

    total = len(services)
    running = sum(1 for s in services if s["status"] == ContainerStatus.RUNNING.value)
    restarting = sum(
        1 for s in services if s["status"] == HealthStatus.RESTARTING.value
    )
    stopped = sum(1 for s in services if s["status"] == ContainerStatus.STOPPED.value)

    unhealthy = sum(
        1
        for s in services
        if s.get("health_status")
        in (HealthStatus.UNHEALTHY.value, HealthStatus.CRASHED.value)
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
@router.get("/logs/stream/{service}")
async def stream_logs(service: str):
    """Server-Sent Events (SSE) endpoint to stream real-time container log lines."""

    async def event_stream():
        try:
            async for line in stream_container_logs(service):
                yield line
        except Exception as exc:
            import traceback

            msg = f"[dockfleet] error streaming logs for {service}: {exc}"
            yield f"data: {msg}\n\n"
            tb = traceback.format_exc()
            for line in tb.splitlines():
                yield f"data: {line}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


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
        "All values reflect the state at collected_at (IST)."
    ),
)
def get_metrics():
    """Return a real-time system metrics snapshot for all services."""
    services = get_services()

    total = len(services)
    running = sum(
        1 for s in services if s.get("health_status") == HealthStatus.HEALTHY.value
    )
    unhealthy = sum(
        1
        for s in services
        if s.get("health_status")
        in (HealthStatus.UNHEALTHY.value, HealthStatus.CRASHED.value)
    )
    stopped = sum(
        1
        for s in services
        if s.get("health_status")
        not in (
            HealthStatus.HEALTHY.value,
            HealthStatus.RESTARTING.value,
            HealthStatus.UNHEALTHY.value,
            HealthStatus.CRASHED.value,
        )
    )
    total_restarts = sum(s.get("restart_count", 0) for s in services)

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with get_session() as session:
        stmt = select(RestartEvent).where(RestartEvent.restarted_at >= since)
        health_failures = len(session.exec(stmt).all())

    collected_utc = datetime.now(timezone.utc)
    return MetricsSummary(
        total_services=total,
        running_services=running,
        unhealthy_services=unhealthy,
        stopped_services=stopped,
        total_restarts=total_restarts,
        health_failures=health_failures,
        collected_at=to_ist_iso(collected_utc),
    )


# ------------------------------------------------
# Analytics: top-level summary
# ------------------------------------------------
@router.get(
    "/analytics/summary",
    response_model=AnalyticsSummary,
    summary="Crash analytics summary",
    description=(
        "Overall stability snapshot — total restarts, failures, and "
        "top unstable services in the requested window."
    ),
)
def analytics_summary(
    limit: int = Query(
        5, ge=1, le=20, description="Max number of unstable services to return"
    ),
    window_hours: int = Query(
        24, ge=1, le=168, description="Look-back window in hours (max 168 = 7 days)"
    ),
):
    """Retrieve overall crash analytics summary within a time window."""
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    base = get_most_unstable_services(limit=limit, window_hours=window_hours)

    with get_session() as session:
        stmt_total = select(RestartEvent).where(RestartEvent.restarted_at >= since)
        all_events = session.exec(stmt_total).all()
        total_restarts = len(all_events)

        health_events = [
            ev for ev in all_events if ev.reason and "health" in ev.reason.lower()
        ]
        total_health_failures = len(health_events)

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
                    last_restart_at=to_ist_iso(last.restarted_at) if last else None,
                )
            )

    return AnalyticsSummary(
        window_hours=window_hours,
        total_restarts=total_restarts,
        total_health_failures=total_health_failures,
        most_unstable_services=unstable,
    )


# ------------------------------------------------
# Analytics: most unstable services
# ------------------------------------------------
@router.get(
    "/analytics/unstable-services",
    response_model=list[UnstableService],
    summary="Most unstable services",
    description="Top N services ranked by restart count within the requested time window.",
)
def analytics_unstable_services(
    limit: int = Query(5, ge=1, le=20, description="Max number of services to return"),
    window_hours: int = Query(
        24, ge=1, le=168, description="Look-back window in hours"
    ),
):
    """Retrieve top unstable services ranked by restart frequency."""
    base = get_most_unstable_services(limit=limit, window_hours=window_hours)

    with get_session() as session:
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
                    last_restart_at=to_ist_iso(last.restarted_at) if last else None,
                )
            )

    return results


# ------------------------------------------------
# Analytics: restart history for a service
# ------------------------------------------------
@router.get(
    "/analytics/restart-history/{service_name}",
    response_model=list[RestartEventItem],
    summary="Restart history for a service",
    description="Returns a list of restart events for the given service, most recent first.",
)
def analytics_restart_history(
    service_name: str,
    since_hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
):
    """Retrieve chronologically ordered restart events for a service."""
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    history = get_restart_history(service_name, since=since)

    return [
        RestartEventItem(
            timestamp=to_ist_iso(item["timestamp"]),
            reason=item["reason"],
            previous_status=item["previous_status"],
            new_status=item["new_status"],
        )
        for item in history
    ]


# ------------------------------------------------
# Settings endpoint (self-healing toggle state)
# ------------------------------------------------
@router.get("/settings")
def settings():
    """Retrieve current orchestrator self-healing setting."""
    orch = get_orchestrator()
    return {"self_healing_enabled": orch.self_healing}


# ------------------------------------------------
# Analytics: failure reason breakdown
# ------------------------------------------------
@router.get(
    "/analytics/failure-reasons/{service_name}",
    response_model=dict[str, int],
    summary="Failure reason breakdown for a service",
    description=(
        "Returns aggregated restart reason counts for a service in the requested "
        "window. Reasons include: healthcheck_timeout, crash_loop, "
        "manual_restart, other."
    ),
)
def analytics_failure_reasons(
    service_name: str,
    window_hours: int = Query(
        24, ge=1, le=168, description="Look-back window in hours"
    ),
):
    """Retrieve count of restart events categorized by failure reason."""
    breakdown = get_failure_reasons_breakdown(
        service_name=service_name,
        window_hours=window_hours,
    )

    return breakdown
