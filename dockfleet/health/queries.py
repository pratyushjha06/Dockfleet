from __future__ import annotations
from collections import Counter
from typing import Any
from sqlmodel import Session, select
from .models import Service, engine

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
          "api": {"cpu": 0.12, "memory": 128_000_000, "uptime": 42},
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
