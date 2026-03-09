from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from .models import Service, RestartEvent, engine

def mark_service_running(name: str) -> None:
    _update_status(name, "running", set_last_health=True)

def mark_service_stopped(name: str) -> None:
    _update_status(name, "stopped", set_last_health=False)

def _update_status(
    name: str,
    new_status: str,
    set_last_health: bool = False,
) -> None:
    """Low-level helper to flip status for a service by name."""
    with Session(engine) as session:
        existing = session.exec(
            select(Service).where(Service.name == name)
        ).one_or_none()

        if existing is None:
            print(
                f"[status] Service '{name}' not found in DB, skipping status update"
            )
            return

        existing.status = new_status

        if set_last_health:
            existing.last_health_check = datetime.utcnow()

        session.add(existing)
        session.commit()

def update_service_health(
    name: str,
    is_healthy: bool,
    reason: Optional[str] = None,
) -> None:
    """
    Update Service row after a health check.

    - If healthy:
        status = "running"
        last_health_check updated
        restart_count unchanged
        consecutive_failures reset to 0
    - If unhealthy:
        status = "unhealthy"
        last_health_check updated
        restart_count++
        consecutive_failures++
        last_failure_reason stored (if provided)
    """
    with Session(engine) as session:
        svc = session.exec(
            select(Service).where(Service.name == name)
        ).one_or_none()

        if svc is None:
            print(f"[health] Service '{name}' not found in DB")
            return

        now = datetime.utcnow()
        svc.last_health_check = now

        if is_healthy:
            # health OK -> treat as running service
            svc.status = "running"
            # if we were failing before, reset the streak
            svc.consecutive_failures = 0
            # restart_count unchanged here
        else:
            svc.status = "unhealthy"
            svc.restart_count += 1
            svc.consecutive_failures += 1
            if reason:
                svc.last_failure_reason = reason

        session.add(svc)
        session.commit()

def needs_restart(service: Service) -> bool:
    """
    Decide if a service should be auto-restarted.
    Rules:
    - At least 3 consecutive health check failures.
    - restart_policy must be "always" or "on-failure".
    - restart_policy == "never" is a hard block.
    """
    if service.consecutive_failures < 3:
        return False

    if service.restart_policy not in {"always", "on-failure"}:
        return False

    return True

def record_restart_event(service: Service, reason: str) -> None:
    """
    Store a simple restart event for later crash analytics.
    Example reason: "3_failed_health_checks".
    """
    # No DB lookup needed; we already have the Service instance.
    event = RestartEvent(
        service_id=service.id,
        restarted_at=datetime.utcnow(),
        reason=reason,
        previous_status=service.status,
        new_status="running",  # intended post-restart status
    )

    with Session(engine) as session:
        session.add(event)
        session.commit()

def mark_restart_successful(service_name: str) -> None:
    """
    Called by orchestrator after a successful auto-restart.
    Resets consecutive_failures and marks status as running.
    """
    with Session(engine) as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()

        if svc is None:
            print(f"[restart] Service '{service_name}' not found in DB")
            return

        svc.consecutive_failures = 0
        svc.status = "running"

        session.add(svc)
        session.commit()