from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from .models import Service, engine

def mark_service_running(name: str) -> None:
    _update_status(name, "running", set_last_health=True)

def mark_service_stopped(name: str) -> None:
    _update_status(name, "stopped", set_last_health=False)

def _update_status(
    name: str,
    new_status: str,
    set_last_health: bool = False,
) -> None:
    # 1) Session open with context manager
    with Session(engine) as session:
        # 2) Service row find karo by name
        existing = session.exec(
            select(Service).where(Service.name == name)
        ).one_or_none()

        # 3) Not found → silently return (ya warning log)
        if existing is None:
            print(
                f"[status] Service '{name}' not found in DB, skipping status update"
            )
            return

        # 4) Status change + optional last_health_check
        existing.status = new_status

        if set_last_health:
            existing.last_health_check = datetime.utcnow()

        # 5) Commit the change
        session.add(existing)
        session.commit()

def update_service_health(
    name: str,
    is_healthy: bool,
    reason: Optional[str] = None,
) -> None:
    """
    Update Service row after a health check.

    - If healthy:  status = "running", update last_health_check,
      restart_count unchanged.
    - If unhealthy: status = "unhealthy", update last_health_check,
      increment restart_count, store last_failure_reason.
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
            # restart_count unchanged
        else:
            svc.status = "unhealthy"
            svc.restart_count += 1
            if reason:
                svc.last_failure_reason = reason

        session.add(svc)
        session.commit()
