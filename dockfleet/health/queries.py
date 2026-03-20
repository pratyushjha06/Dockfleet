from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional
from sqlmodel import Session, select, func
from .models import Service, RestartEvent, LogEvent, engine

def get_all_services() -> list[Service]:
    """
    Low-level helper: return all Service rows from SQLite.
    Dashboard/API layer can either use these ORM objects directly
    or call get_services_for_dashboard() for JSON-ready dicts.
    """
    with Session(engine) as session:
        services = session.exec(select(Service)).all()
    return services

def get_services_for_dashboard() -> list[dict[str, Any]]:
    """
    High-level helper: return services as plain dicts suitable
    for the /services dashboard API response.
    These dicts can be easily enriched with orchestrator stats
    (CPU, memory, uptime) before sending to the frontend.
    """
    services = get_all_services()

    result: list[dict[str, Any]] = []
    for svc in services:
        result.append(
            {
                "name": svc.name,
                "status": svc.status,
                "restart_count": svc.restart_count,
                "last_health_check": svc.last_health_check,
                "consecutive_failures": svc.consecutive_failures,
                "last_failure_reason": svc.last_failure_reason,
                "image": svc.image,
                "ports": svc.ports_raw,
                "restart_policy": svc.restart_policy,
                "resources_memory": svc.resources_memory,
                "resources_cpu": svc.resources_cpu,
                "env": svc.env_raw,
                "depends_on": svc.depends_on_raw,
            }
        )
    return result

def get_services_for_dashboard_with_stats(
    stats_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Enrich DB-based service state with Docker runtime stats
    (CPU, memory, uptime, etc.), for the /services API.

    stats_by_name example:
        {
          "api":   {"cpu": 0.12, "memory": 128_000_000, "uptime": 42},
          "redis": {"cpu": 0.01, "memory": 32_000_000, "uptime": 120},
        }

    Convention:
    - cpu: fractional (0.12 = 12% CPU)
    - memory: bytes
    - uptime: seconds
    """
    base = get_services_for_dashboard()
    enriched: list[dict[str, Any]] = []

    for svc in base:
        name = svc["name"]
        stats = stats_by_name.get(name, {})

        enriched.append(
            {
                **svc,
                "cpu": stats.get("cpu"),
                "memory": stats.get("memory"),
                "uptime": stats.get("uptime"),
            }
        )

    return enriched

def get_status_counts() -> dict[str, int]:
    """
    Return a simple count of services per status, e.g.:
    {"running": 2, "unhealthy": 1, "stopped": 1}
    Useful for dashboard summary bar.
    """
    services = get_all_services()
    counter: Counter[str] = Counter()

    for svc in services:
        counter[svc.status or "unknown"] += 1

    return dict(counter)


# -------------------------------------------------------------------
# Crash analytics helpers
# -------------------------------------------------------------------


def get_restart_history(
    service_name: str,
    since: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """
    Return restart history for a service as list of dicts:
    [
      {
        "timestamp": <datetime>,
        "reason": "3_failed_health_checks" | "manual_dashboard_restart" | ...,
        "previous_status": "...",
        "new_status": "..."
      },
      ...
    ]
    """
    with Session(engine) as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()
        if svc is None:
            return []

        stmt = select(RestartEvent).where(
            RestartEvent.service_id == svc.id
        )

        if since is not None:
            stmt = stmt.where(RestartEvent.restarted_at >= since)

        stmt = stmt.order_by(RestartEvent.restarted_at.desc())
        events = session.exec(stmt).all()

        return [
            {
                "timestamp": ev.restarted_at,
                "reason": ev.reason,
                "previous_status": ev.previous_status,
                "new_status": ev.new_status,
            }
            for ev in events
        ]

def get_most_unstable_services(
    limit: int = 5,
    window_hours: int = 24,
) -> list[dict[str, Any]]:
    """
    Return services ordered by number of RestartEvent in the last `window_hours`.
    Output example:
      [
        {"service_name": "api", "restarts": 3},
        {"service_name": "worker", "restarts": 1},
      ]
    """
    since = datetime.utcnow() - timedelta(hours=window_hours)

    with Session(engine) as session:
        stmt = (
            select(Service.name, func.count(RestartEvent.id))
            .join(RestartEvent, RestartEvent.service_id == Service.id)
            .where(RestartEvent.restarted_at >= since)
            .group_by(Service.id, Service.name)
            .order_by(func.count(RestartEvent.id).desc())
            .limit(limit)
        )

        rows = session.exec(stmt).all()

        return [
            {
                "service_name": name,
                "restarts": count,
            }
            for name, count in rows
        ]

def normalize_failure_reason(raw: str | None) -> str:
    """
    Map raw RestartEvent.reason into a small set of categories
    for clearer crash analytics.
    """
    text = (raw or "").lower()

    if "3_failed_health_checks" in text or "health_check" in text:
        return "healthcheck_timeout"

    if "auto-restart failed" in text or "crash" in text:
        return "crash_loop"

    if "manual_dashboard_restart" in text or "manual" in text:
        return "manual_restart"

    return "other"


def get_failure_reasons_breakdown(
    service_name: str,
    window_hours: int = 24,
) -> dict[str, int]:
    """
    Aggregate restart reasons (grouped into categories) for a service
    in the last `window_hours`.
    """
    since = datetime.utcnow() - timedelta(hours=window_hours)

    with Session(engine) as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()
        if svc is None:
            return {}

        stmt = (
            select(RestartEvent.reason)
            .where(RestartEvent.service_id == svc.id)
            .where(RestartEvent.restarted_at >= since)
        )

        rows = session.exec(stmt).all()

    counts: dict[str, int] = {}
    for raw_reason in rows:
        category = normalize_failure_reason(raw_reason)
        counts[category] = counts.get(category, 0) + 1

    return counts
