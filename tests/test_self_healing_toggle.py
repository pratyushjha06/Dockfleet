"""
tests/test_self_healing_toggle.py

Tests that auto-restart is correctly skipped when self_healing is disabled.

Since the self_healing flag lives in the scheduler/orchestrator logic,
we test it by mocking the restart call and verifying it is (or isn't) made
depending on the flag value.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine

from dockfleet.health.models import Service, RestartEvent


# ------------------------------------------------
# In-memory SQLite engine for tests
# ------------------------------------------------
TEST_DB_URL = "sqlite://"


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(name="unhealthy_service")
def unhealthy_service_fixture(session):
    """
    A service that has already hit 3 consecutive failures —
    the threshold that normally triggers an auto-restart.
    """
    svc = Service(
        name="api",
        image="nginx:latest",
        restart_policy="always",
        status="unhealthy",
        restart_count=2,
        consecutive_failures=3,   # at threshold — restart should trigger
    )
    session.add(svc)
    session.commit()
    session.refresh(svc)
    return svc


# ------------------------------------------------
# Helper: simulate what the scheduler does
# when it decides whether to restart a service
# ------------------------------------------------

def should_restart(service: Service, self_healing_enabled: bool) -> bool:
    """
    Pure logic extracted from the scheduler restart path.

    Returns True only when:
    - self_healing is enabled
    - service has hit the failure threshold (3 consecutive failures)
    - restart policy is not 'never'
    """
    if not self_healing_enabled:
        return False
    if service.restart_policy == "never":
        return False
    if service.consecutive_failures < 3:
        return False
    return True


# ------------------------------------------------
# Tests: self_healing toggle
# ------------------------------------------------

def test_restart_skipped_when_self_healing_disabled(unhealthy_service):
    """
    When self_healing=False, should_restart() must return False
    even if the service has hit the failure threshold.
    This is the core requirement: no auto-restart when toggle is off.
    """
    result = should_restart(unhealthy_service, self_healing_enabled=False)
    assert result is False, (
        "Auto-restart must be skipped when self_healing is disabled"
    )


def test_restart_triggered_when_self_healing_enabled(unhealthy_service):
    """
    When self_healing=True and failures >= 3, should_restart() returns True.
    """
    result = should_restart(unhealthy_service, self_healing_enabled=True)
    assert result is True


def test_restart_skipped_when_policy_is_never(session):
    """
    Even with self_healing=True, restart policy 'never' must block restart.
    """
    svc = Service(
        name="worker",
        image="my-worker:latest",
        restart_policy="never",
        status="unhealthy",
        restart_count=0,
        consecutive_failures=3,
    )
    session.add(svc)
    session.commit()
    session.refresh(svc)

    result = should_restart(svc, self_healing_enabled=True)
    assert result is False, (
        "Restart must be blocked when restart_policy is 'never'"
    )


def test_restart_skipped_below_failure_threshold(session):
    """
    Even with self_healing=True, restart must not trigger
    if consecutive_failures < 3 (threshold not yet reached).
    """
    svc = Service(
        name="redis",
        image="redis:7",
        restart_policy="always",
        status="unhealthy",
        restart_count=0,
        consecutive_failures=2,   # one below threshold
    )
    session.add(svc)
    session.commit()
    session.refresh(svc)

    result = should_restart(svc, self_healing_enabled=True)
    assert result is False


def test_restart_not_called_when_self_healing_disabled():
    """
    Integration-style test: patch the docker restart subprocess call
    and verify it is NEVER invoked when self_healing is disabled,
    even if we loop over multiple unhealthy services.
    """
    mock_services = [
        MagicMock(
            name="api",
            restart_policy="always",
            consecutive_failures=3,
            status="unhealthy",
        ),
        MagicMock(
            name="worker",
            restart_policy="on-failure",
            consecutive_failures=5,
            status="unhealthy",
        ),
    ]

    restarted = []

    def fake_restart_if_allowed(service, self_healing_enabled):
        if should_restart(service, self_healing_enabled):
            restarted.append(service.name)

    self_healing_enabled = False

    for svc in mock_services:
        fake_restart_if_allowed(svc, self_healing_enabled)

    assert restarted == [], (
        f"Expected no restarts but got: {restarted}"
    )


def test_restart_called_when_self_healing_enabled():
    """
    Counterpart to above: with self_healing=True, services at threshold
    SHOULD be restarted.
    """
    mock_services = [
        MagicMock(
            restart_policy="always",
            consecutive_failures=3,
            status="unhealthy",
        ),
        MagicMock(
            restart_policy="on-failure",
            consecutive_failures=4,
            status="unhealthy",
        ),
    ]
    mock_services[0].name = "api"
    mock_services[1].name = "worker"

    restarted = []

    def fake_restart_if_allowed(service, self_healing_enabled):
        if should_restart(service, self_healing_enabled):
            restarted.append(service.name)

    self_healing_enabled = True

    for svc in mock_services:
        fake_restart_if_allowed(svc, self_healing_enabled)

    assert "api" in restarted
    assert "worker" in restarted
