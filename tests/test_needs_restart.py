import pytest
from sqlmodel import Session, select
from sqlalchemy import text
from dockfleet.health.models import Service, init_db, engine
from dockfleet.health.status import (
    update_service_health,
    needs_restart,
    mark_restart_successful,
)

def _create_service(
    name: str = "api",
    restart_policy: str = "always",
) -> Service:
    svc = Service(
        name=name,
        image="dummy-image",
        restart_policy=restart_policy,
        status="running",
    )
    with Session(engine) as session:
        session.add(svc)
        session.commit()
    return svc

def _get_service(name: str) -> Service:
    with Session(engine) as session:
        return session.exec(
            select(Service).where(Service.name == name)
        ).one()

def setup_function() -> None:
    """
    Per-test reset: ensure schema exists and clear Service rows.
    """
    init_db()

    # Hard clear Service table so unique(name) doesn't collide
    with Session(engine) as session:
        session.exec(text("DELETE FROM service"))
        session.commit()

def test_needs_restart_after_three_failures_with_always_policy() -> None:
    _create_service(name="svc1", restart_policy="always")

    # simulate 3 consecutive unhealthy checks
    update_service_health("svc1", is_healthy=False, reason="fail 1")
    update_service_health("svc1", is_healthy=False, reason="fail 2")
    update_service_health("svc1", is_healthy=False, reason="fail 3")

    svc = _get_service("svc1")

    assert svc.consecutive_failures == 3
    assert svc.status == "unhealthy"
    assert needs_restart(svc) is True

def test_needs_restart_after_three_failures_with_on_failure_policy() -> None:
    _create_service(name="svc2", restart_policy="on-failure")

    update_service_health("svc2", is_healthy=False, reason="fail 1")
    update_service_health("svc2", is_healthy=False, reason="fail 2")
    update_service_health("svc2", is_healthy=False, reason="fail 3")

    svc = _get_service("svc2")

    assert svc.consecutive_failures == 3
    assert svc.status == "unhealthy"
    assert needs_restart(svc) is True

def test_needs_restart_false_before_threshold() -> None:
    _create_service(name="svc3", restart_policy="always")

    # only 2 failures -> should NOT trigger restart yet
    update_service_health("svc3", is_healthy=False, reason="fail 1")
    update_service_health("svc3", is_healthy=False, reason="fail 2")

    svc = _get_service("svc3")

    assert svc.consecutive_failures == 2
    assert svc.status == "unhealthy"
    assert needs_restart(svc) is False

def test_needs_restart_respects_never_policy() -> None:
    _create_service(name="svc4", restart_policy="never")

    # even with 3 failures, never policy must block restart
    update_service_health("svc4", is_healthy=False, reason="fail 1")
    update_service_health("svc4", is_healthy=False, reason="fail 2")
    update_service_health("svc4", is_healthy=False, reason="fail 3")

    svc = _get_service("svc4")

    assert svc.consecutive_failures == 3
    assert svc.status == "unhealthy"
    assert needs_restart(svc) is False

def test_consecutive_failures_reset_after_successful_restart() -> None:
    _create_service(name="svc5", restart_policy="always")

    # drive it to restart threshold
    update_service_health("svc5", is_healthy=False, reason="fail 1")
    update_service_health("svc5", is_healthy=False, reason="fail 2")
    update_service_health("svc5", is_healthy=False, reason="fail 3")

    svc_before = _get_service("svc5")
    assert svc_before.consecutive_failures == 3
    assert needs_restart(svc_before) is True

    # simulate orchestrator successfully restarting the service
    mark_restart_successful("svc5")

    svc_after = _get_service("svc5")
    assert svc_after.consecutive_failures == 0
    assert svc_after.status == "running"
    assert needs_restart(svc_after) is False
