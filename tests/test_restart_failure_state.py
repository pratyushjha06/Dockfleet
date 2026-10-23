"""Regression tests for the state written after a failed auto-restart.

A failed restart used to set status=STOPPED, which the health engine treats as
an intentional operator stop.  The service was then skipped by the scheduler
and its failures were ignored forever, so a single transient Docker error
disabled self-healing.  A failed restart now leaves the state UNKNOWN, which is
restart-eligible, and a later success clears the stale failure reason.
"""

from sqlmodel import select

from dockfleet.cli.config import (
    DockFleetConfig,
    HealthCheckConfig,
    RestartPolicy,
    ServiceConfig,
)
from dockfleet.core.orchestrator import mark_restart_failed
from dockfleet.health.models import (
    ContainerStatus,
    HealthStatus,
    Service,
    get_session,
)
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.status import (
    mark_restart_successful,
    needs_restart,
    update_service_health,
)


def _create_service(
    status: ContainerStatus = ContainerStatus.RUNNING,
    failures: int = 0,
    health: HealthStatus = HealthStatus.HEALTHY,
    reason: str | None = None,
) -> None:
    service = Service(
        name="api",
        image="dummy-image",
        restart_policy="always",
        status=status,
        consecutive_failures=failures,
        health_status=health,
        last_failure_reason=reason,
    )
    with get_session() as session:
        session.add(service)
        session.commit()


def _load_service() -> Service:
    with get_session() as session:
        return session.exec(select(Service).where(Service.name == "api")).one()


def _fail_three_times() -> None:
    for attempt in range(1, 4):
        update_service_health("api", False, f"failure {attempt}")


def test_failed_restart_marks_unknown_and_stays_restart_eligible():
    _create_service()
    _fail_three_times()

    mark_restart_failed("api", "port 8000 is already allocated")

    svc = _load_service()
    assert svc.status == ContainerStatus.UNKNOWN
    assert svc.health_status == HealthStatus.CRASHED
    assert (svc.last_failure_reason or "").startswith("auto-restart failed:")
    assert needs_restart(svc) is True


def test_failed_checks_keep_counting_after_a_failed_restart():
    _create_service()
    _fail_three_times()

    mark_restart_failed("api", "docker daemon hiccup")
    update_service_health("api", False, "still down")

    svc = _load_service()
    assert svc.consecutive_failures == 4
    assert svc.health_status == HealthStatus.CRASHED
    assert needs_restart(svc) is True


def test_successful_restart_clears_the_failure_reason():
    _create_service(
        status=ContainerStatus.UNKNOWN,
        failures=3,
        health=HealthStatus.CRASHED,
        reason="auto-restart failed: boom",
    )

    mark_restart_successful("api")

    svc = _load_service()
    assert svc.status == ContainerStatus.RUNNING
    assert svc.health_status == HealthStatus.HEALTHY
    assert svc.consecutive_failures == 0
    assert svc.last_failure_reason is None


def _restart_config(**options) -> DockFleetConfig:
    values = {
        "image": "dummy-image",
        "restart": RestartPolicy.always,
        "healthcheck": HealthCheckConfig(type="process", interval=5),
    }
    values.update(options)
    return DockFleetConfig(services={"api": ServiceConfig(**values)})


def _retry_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dockfleet.health.scheduler.restart_service",
        lambda *args, **kwargs: calls.append(args) or True,
    )
    return calls


def test_retry_loop_still_honors_max_restarts(monkeypatch):
    _create_service(
        status=ContainerStatus.UNKNOWN,
        failures=3,
        health=HealthStatus.CRASHED,
        reason="auto-restart failed: boom",
    )
    scheduler = HealthScheduler(
        config=_restart_config(max_restarts=1), interval_seconds=5
    )
    scheduler._restart_attempts["api"] = 1
    calls = _retry_calls(monkeypatch)

    scheduler._handle_post_health("api")

    assert calls == []


def test_retry_loop_still_honors_backoff(monkeypatch):
    _create_service(
        status=ContainerStatus.UNKNOWN,
        failures=3,
        health=HealthStatus.CRASHED,
        reason="auto-restart failed: boom",
    )
    scheduler = HealthScheduler(
        config=_restart_config(backoff_seconds=30), interval_seconds=5
    )
    calls = _retry_calls(monkeypatch)

    scheduler._handle_post_health("api")

    assert calls == []
    assert "api" in scheduler._next_restart_at
