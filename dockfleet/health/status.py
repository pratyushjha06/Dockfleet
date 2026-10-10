from datetime import datetime, timezone

from sqlmodel import select

from .models import (
    ContainerStatus,
    HealthStatus,
    RestartEvent,
    Service,
    get_session,
)


def mark_service_running(name: str) -> None:
    """
    Mark a service as actively running and healthy in the database.

    Sets container lifecycle status to RUNNING and health_status to HEALTHY,
    recording the current UTC timestamp in last_health_check.
    """
    _update_status(
        name,
        new_status=ContainerStatus.RUNNING,
        new_health=HealthStatus.HEALTHY,
        set_last_health=True,
    )


def mark_service_stopped(name: str) -> None:
    """
    Mark a service as cleanly stopped in the database.

    Sets container lifecycle status to STOPPED while preserving healthy status
    (normal intentional stop).
    """
    _update_status(
        name,
        new_status=ContainerStatus.STOPPED,
        new_health=HealthStatus.HEALTHY,
        set_last_health=False,
    )


def _update_status(
    name: str,
    new_status: ContainerStatus | str,
    new_health: HealthStatus | str | None = None,
    set_last_health: bool = False,
) -> None:
    """Low-level helper to flip status (and optionally health_status) for a service by name."""
    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == name)).one_or_none()

        if svc is None:
            print(f"[status] Service '{name}' not found in DB, skipping status update")
            return

        svc.status = (
            ContainerStatus(new_status)
            if isinstance(new_status, str)
            and not isinstance(new_status, ContainerStatus)
            else new_status
        )

        if new_health is not None:
            svc.health_status = (
                HealthStatus(new_health)
                if isinstance(new_health, str)
                and not isinstance(new_health, HealthStatus)
                else new_health
            )

        if set_last_health:
            svc.last_health_check = datetime.now(timezone.utc)

        session.add(svc)
        session.commit()


def update_service_health(
    name: str,
    is_healthy: bool,
    reason: str | None = None,
) -> None:
    """
    Update Service row after a health check.
    - If healthy:
        status        = ContainerStatus.RUNNING
        health_status = HealthStatus.HEALTHY
        last_health_check updated
        consecutive_failures reset to 0
    - If unhealthy:
        If svc.status is STOPPED, failed health check responses are ignored
        and do not mutate health state into CRASHED or increment consecutive_failures.
        Otherwise:
        status        stays as-is (running/stopped decided elsewhere)
        health_status = HealthStatus.CRASHED (if >= 3 failures) or UNHEALTHY
        last_health_check updated
        consecutive_failures++
    """
    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == name)).one_or_none()

        if svc is None:
            print(f"[health] Service '{name}' not found in DB")
            return

        if not is_healthy and svc.status in (
            ContainerStatus.STOPPED,
            ContainerStatus.STOPPED.value,
        ):
            return

        now = datetime.now(timezone.utc)
        svc.last_health_check = now

        if is_healthy:
            if (
                svc.status != ContainerStatus.STOPPED
                and svc.status != ContainerStatus.STOPPED.value
            ):
                svc.status = ContainerStatus.RUNNING
            svc.health_status = HealthStatus.HEALTHY
            svc.consecutive_failures = 0
            svc.last_failure_reason = None
        else:
            svc.consecutive_failures += 1
            if svc.consecutive_failures >= 3:
                svc.health_status = HealthStatus.CRASHED
            else:
                svc.health_status = HealthStatus.UNHEALTHY
            if reason:
                svc.last_failure_reason = reason

        session.add(svc)
        session.commit()


def needs_restart(service: Service) -> bool:
    """
    Decide if a service should be auto-restarted.
    Rules:
    - Do not restart if container is intentionally stopped (ContainerStatus.STOPPED).
    - At least 3 consecutive health check failures.
    - restart_policy must be "always" or "on-failure".
    - restart_policy == "never" is a hard block.
    - Only restart if currently unhealthy or crashed.
    """
    if service.status in (ContainerStatus.STOPPED, ContainerStatus.STOPPED.value):
        return False

    if service.consecutive_failures < 3:
        return False

    policy = (
        str(getattr(service.restart_policy, "value", service.restart_policy))
        .strip()
        .lower()
        .replace("_", "-")
        if service.restart_policy is not None
        else ""
    )
    if policy not in {"always", "on-failure"}:
        return False

    if service.health_status not in {HealthStatus.UNHEALTHY, HealthStatus.CRASHED}:
        return False

    return True


def record_restart_event(service: Service, reason: str) -> None:
    """
    Store a simple restart event for later crash analytics in DB.
    Example reason: "3_failed_health_checks".
    """
    with get_session() as session:
        svc = None
        if getattr(service, "id", None) is not None:
            svc = session.exec(
                select(Service).where(Service.id == service.id)
            ).one_or_none()
        if svc is None and getattr(service, "name", None):
            svc = session.exec(
                select(Service).where(Service.name == service.name)
            ).one_or_none()

        if svc is not None:
            service_id = svc.id
            service_name = svc.name
            previous_status = (
                svc.status.value
                if isinstance(svc.status, ContainerStatus)
                else svc.status
            )
        else:
            service_id = getattr(service, "id", None)
            service_name = getattr(service, "name", "")
            previous_status = (
                service.status.value
                if isinstance(service.status, ContainerStatus)
                else getattr(service, "status", None)
            )

        event = RestartEvent(
            service_id=service_id,
            service_name=service_name,
            restarted_at=datetime.now(timezone.utc),
            reason=reason,
            previous_status=previous_status,
            new_status=ContainerStatus.RUNNING.value,  # intended post-restart status
        )

        session.add(event)
        session.commit()


def mark_restart_successful(service_name: str) -> None:
    """
    Called by orchestrator after a successful auto-restart.
    Resets consecutive_failures and marks service as healthy + running.
    """
    with get_session() as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()

        if svc is None:
            print(f"[restart] Service '{service_name}' not found in DB")
            return

        svc.consecutive_failures = 0
        svc.status = ContainerStatus.RUNNING
        svc.health_status = HealthStatus.HEALTHY

        session.add(svc)
        session.commit()


def record_manual_restart_event(service_name: str) -> None:
    """
    Called when a manual restart is triggered from the dashboard
    and the orchestrator has successfully restarted the container.

    - Marks status as ContainerStatus.RUNNING and health_status as HealthStatus.HEALTHY.
    - Resets consecutive_failures to 0.
    - Inserts a RestartEvent with reason='manual_dashboard_restart'.
    """
    with get_session() as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()

        if svc is None:
            print(f"[manual-restart] Service '{service_name}' not found in DB")
            return

        previous_status = (
            svc.status.value if isinstance(svc.status, ContainerStatus) else svc.status
        )
        svc.status = ContainerStatus.RUNNING
        svc.health_status = HealthStatus.HEALTHY
        svc.consecutive_failures = 0

        event = RestartEvent(
            service_id=svc.id,
            service_name=svc.name,
            restarted_at=datetime.now(timezone.utc),
            reason="manual_dashboard_restart",
            previous_status=previous_status,
            new_status=ContainerStatus.RUNNING.value,
        )

        session.add(svc)
        session.add(event)
        session.commit()


def record_manual_stop(service_name: str) -> None:
    """
    Called when a manual stop is triggered from the dashboard
    and the orchestrator has successfully stopped the container.

    - Marks status as ContainerStatus.STOPPED.
    - Keeps health_status as HealthStatus.HEALTHY (it's a clean stop).
    - Does NOT touch restart_count or consecutive_failures.
    """
    with get_session() as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()

        if svc is None:
            print(f"[manual-stop] Service '{service_name}' not found in DB")
            return

        svc.status = ContainerStatus.STOPPED
        svc.health_status = HealthStatus.HEALTHY

        session.add(svc)
        session.commit()
