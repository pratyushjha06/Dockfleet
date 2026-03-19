import subprocess
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Query, Request
from fastapi.responses import (
    HTMLResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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
# Service schema (for documentation / typing)
# ------------------------------------------------
class Service(BaseModel):
    name: str
    status: str
    health_status: str
    image: str
    ports: str | None
    restart_policy: str
    restart_count: int
    last_health_check: Optional[datetime] = None

    cpu: Optional[str] = None
    memory: Optional[str] = None
    uptime: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_limit: Optional[str] = None


class ActionResponse(BaseModel):
    ok: bool
    message: str


# ------------------------------------------------
# Crash Analytics Pydantic models
# ------------------------------------------------
class UnstableService(BaseModel):
    service_name: str
    restarts: int
    last_restart_at: Optional[datetime] = None


class RestartEventItem(BaseModel):
    timestamp: datetime
    reason: str
    previous_status: Optional[str] = None
    new_status: Optional[str] = None


class FailureReasonCount(BaseModel):
    reason: str
    count: int


class AnalyticsSummary(BaseModel):
    """
    Top-level crash analytics summary.
    Returned by /analytics/summary – gives a single snapshot
    of system stability: most unstable services plus overall
    restart and failure counts in the requested window.
    """
    window_hours: int
    total_restarts: int
    total_health_failures: int
    most_unstable_services: List[UnstableService]


# ------------------------------------------------
# Metrics Pydantic model
# ------------------------------------------------
class MetricsSummary(BaseModel):
    """
    System-level metrics snapshot.
    Returned by /metrics – gives a quick health overview of
    the entire DockFleet deployment: service counts and
    aggregate restart / failure totals.
    """
    total_services: int
    running_services: int
    unhealthy_services: int
    stopped_services: int
    total_restarts: int
    health_failures: int
    collected_at: datetime


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
# Combines DB state + Docker runtime stats
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

    result = subprocess.run(
        ["docker", "restart", container],
        capture_output=True,
    )

    ok = result.returncode == 0

    if ok:
        record_manual_restart_event(name)

    return {
        "message": f"{name} restarted",
        "ok": ok,
    }


# ------------------------------------------------
# Stop service
# ------------------------------------------------
@router.post("/services/{name}/stop", response_model=ActionResponse)
def stop_service(name: str):
    container = f"dockfleet_{name}"

    result = subprocess.run(
        ["docker", "stop", container],
        capture_output=True,
    )

    ok = result.returncode == 0

    if ok:
        record_manual_stop(name)

    return {
        "message": f"{name} stopped",
        "ok": ok,
    }


# ------------------------------------------------
# DB-backed logs metadata API
# ------------------------------------------------
@router.get("/logs/db")
def list_logs(
    service_name: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    events = query_logs(
        service_name=service_name,
        q=q,
        limit=limit,
        offset=offset,
    )

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
# Legacy /logs: live docker logs for a service
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
# Download logs from DB (streaming)
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
# System summary for dashboard
# ------------------------------------------------
@router.get("/status")
def system_status():
    services = get_services()

    total = len(services)
    running = sum(1 for s in services if s["health_status"] == "healthy")
    restarting = sum(1 for s in services if s["health_status"] == "restarting")
    unhealthy = sum(1 for s in services if s["health_status"] == "unhealthy")
    stopped = sum(
        1
        for s in services
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

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )


# ------------------------------------------------
# Metrics endpoint
# ------------------------------------------------
@router.get("/metrics", response_model=MetricsSummary)
def get_metrics():
    """
    Return a system-level metrics snapshot for DockFleet.

    Includes:
    - total_services: all services registered in DB
    - running_services: services currently healthy
    - unhealthy_services: services currently unhealthy
    - stopped_services: services that are stopped
    - total_restarts: sum of restart_count across all services
    - health_failures: count of RestartEvents in the last 24 hours
    - collected_at: UTC timestamp of when metrics were collected
    """
    services = get_services()

    total = len(services)
    running = sum(1 for s in services if s["health_status"] == "healthy")
    unhealthy = sum(1 for s in services if s["health_status"] == "unhealthy")
    stopped = sum(
        1
        for s in services
        if s["health_status"] not in ["healthy", "restarting", "unhealthy"]
    )
    total_restarts = sum(s.get("restart_count", 0) for s in services)

    # Count restart events in last 24 hours as proxy for health failures
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
# Crash analytics endpoints
# ------------------------------------------------
@router.get(
    "/analytics/summary",
    response_model=AnalyticsSummary,
)
def analytics_summary(
    limit: int = Query(5, ge=1, le=20),
    window_hours: int = Query(24, ge=1, le=168),
):
    """
    Return a top-level crash analytics summary.

    Includes total restarts, total health failures, and the
    most unstable services in the requested time window.
    """
    since = datetime.utcnow() - timedelta(hours=window_hours)

    base = get_most_unstable_services(limit=limit, window_hours=window_hours)

    with Session(engine) as session:
        # Total restart events in window
        stmt_total = select(RestartEvent).where(
            RestartEvent.restarted_at >= since
        )
        all_events = session.exec(stmt_total).all()
        total_restarts = len(all_events)

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
        total_health_failures=total_restarts,  # proxy: each restart = failure
        most_unstable_services=unstable,
    )


@router.get(
    "/analytics/unstable-services",
    response_model=List[UnstableService],
)
def analytics_unstable_services(
    limit: int = Query(5, ge=1, le=20),
    window_hours: int = Query(24, ge=1, le=168),
):
    """
    Return top N most unstable services (by restart count in last window_hours)
    plus last restart timestamp for each.
    """
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


@router.get(
    "/analytics/restart-history/{service_name}",
    response_model=List[RestartEventItem],
)
def analytics_restart_history(
    service_name: str,
    since_hours: int = Query(24, ge=1, le=168),
):
    """
    Return recent restart events for a given service.
    """
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


@router.get(
    "/analytics/failure-reasons/{service_name}",
    response_model=List[FailureReasonCount],
)
def analytics_failure_reasons(
    service_name: str,
    window_hours: int = Query(24, ge=1, le=168),
):
    """
    Aggregate restart reasons for a service in the last window_hours.
    Returns grouped categories like healthcheck_timeout, crash_loop,
    manual_restart, other.
    """
    breakdown = get_failure_reasons_breakdown(
        service_name=service_name,
        window_hours=window_hours,
    )

    items = sorted(
        breakdown.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )

    return [
        FailureReasonCount(reason=reason, count=count)
        for reason, count in items
    ]
    