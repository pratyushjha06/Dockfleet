import time

import pytest
from sqlalchemy import text
from sqlmodel import Session

from dockfleet.cli.config import (
    DockFleetConfig,
    HealthCheckConfig,
    ServiceConfig,
)
from dockfleet.health.models import (
    ContainerStatus,
    Service,
    get_session,
    init_db,
)
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.status import update_service_health


def _build_config(**service_options) -> DockFleetConfig:
    options = {
        "image": "dummy-image",
        "restart": "always",
        "healthcheck": HealthCheckConfig(
            type="process",
            cmd="dummy-process",
            interval=1,
        ),
    }
    options.update(service_options)

    return DockFleetConfig(
        version="1.0",
        services={"api": ServiceConfig(**options)},
    )


def _create_service(name: str = "api") -> None:
    service = Service(
        name=name,
        image="dummy-image",
        restart_policy="always",
        status=ContainerStatus.RUNNING,
    )

    with get_session() as session:
        session.add(service)
        session.commit()


def _fail_service(name: str = "api") -> None:
    for i in range(3):
        update_service_health(
            name,
            is_healthy=False,
            reason=f"failure {i + 1}",
        )


def setup_function() -> None:
    init_db()

    with get_session() as session:
        session.exec(text("DELETE FROM service"))
        session.commit()


def test_default_settings_preserve_existing_restart_behavior(monkeypatch) -> None:
    config = _build_config()

    assert config.services["api"].max_restarts is None
    assert config.services["api"].backoff_seconds is None
    assert config.services["api"].backoff_multiplier is None

    _create_service()
    _fail_service()

    calls = []

    def fake_restart(name, config, detailed=False):
        calls.append(name)
        return True

    monkeypatch.setattr(
        "dockfleet.health.scheduler.restart_service",
        fake_restart,
    )

    scheduler = HealthScheduler(config=config)

    scheduler._handle_post_health("api")

    assert calls == ["api"]
    assert scheduler._restart_attempts["api"] == 1


def test_exponential_backoff_increases_between_restart_attempts(
    monkeypatch,
) -> None:
    config = _build_config(
        backoff_seconds=10,
        backoff_multiplier=2,
    )

    _create_service()
    _fail_service()

    calls = []

    def fake_restart(name, config, detailed=False):
        calls.append(name)
        return True

    monkeypatch.setattr(
        "dockfleet.health.scheduler.restart_service",
        fake_restart,
    )

    current_time = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: current_time)

    scheduler = HealthScheduler(config=config)

    # First restart waits for the initial 10 second delay.
    scheduler._handle_post_health("api")

    assert calls == []
    assert scheduler._next_restart_at["api"] == 110.0

    # Still inside the first backoff period.
    current_time = 109.0
    scheduler._handle_post_health("api")

    assert calls == []

    # Backoff expired, so the first restart happens.
    current_time = 110.0
    scheduler._handle_post_health("api")

    assert calls == ["api"]
    assert scheduler._restart_attempts["api"] == 1

    # Simulate another recovery cycle that fails again.
    _fail_service()

    current_time = 200.0
    scheduler._handle_post_health("api")

    # Second delay = 10 * 2^1 = 20 seconds.
    assert calls == ["api"]
    assert scheduler._next_restart_at["api"] == 220.0


def test_max_restarts_stops_repeated_automatic_restarts(monkeypatch) -> None:
    config = _build_config(max_restarts=1)

    _create_service()
    _fail_service()

    calls = []

    def fake_restart(name, config, detailed=False):
        calls.append(name)
        return True

    monkeypatch.setattr(
        "dockfleet.health.scheduler.restart_service",
        fake_restart,
    )

    scheduler = HealthScheduler(config=config)

    # First recovery-cycle restart is allowed.
    scheduler._handle_post_health("api")

    assert calls == ["api"]
    assert scheduler._restart_attempts["api"] == 1

    # Service fails again during the same recovery cycle.
    _fail_service()

    # Restart limit has now been reached.
    scheduler._handle_post_health("api")

    assert calls == ["api"]


def test_recovery_state_resets_after_healthy_check(monkeypatch) -> None:
    config = _build_config()

    config.services["api"].healthcheck = HealthCheckConfig(
        type="process",
        endpoint="ignored",
        interval=60,
    )

    _create_service()

    class HealthyChecker:
        def check_process(self, container_name: str) -> bool:
            return True

    scheduler = HealthScheduler(
        config=config,
        interval_seconds=1,
        checker=HealthyChecker(),
    )

    scheduler._restart_attempts["api"] = 2
    scheduler._next_restart_at["api"] = 123.0
    scheduler._stopped = False

    def stop_after_tick(_seconds: float) -> None:
        scheduler._stopped = True

    monkeypatch.setattr(time, "sleep", stop_after_tick)

    scheduler._poll()

    assert "api" not in scheduler._restart_attempts
    assert "api" not in scheduler._next_restart_at


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_restarts", 0),
        ("max_restarts", -1),
        ("backoff_seconds", -1),
        ("backoff_multiplier", 0),
        ("backoff_multiplier", 0.5),
    ],
)
def test_invalid_restart_configuration_values(field, value) -> None:
    with pytest.raises(ValueError):
        ServiceConfig(
            image="dummy-image",
            restart="always",
            **{field: value},
        )


def test_failed_restart_counts_toward_restart_limit(monkeypatch) -> None:
    config = _build_config(max_restarts=1)

    _create_service()
    _fail_service()

    calls = []

    def fake_restart(name, config, detailed=False):
        calls.append(name)
        return False

    monkeypatch.setattr(
        "dockfleet.health.scheduler.restart_service",
        fake_restart,
    )

    scheduler = HealthScheduler(config=config)

    # Failed restart still counts as an automatic restart attempt.
    scheduler._handle_post_health("api")

    assert calls == ["api"]
    assert scheduler._restart_attempts["api"] == 1
    assert "api" not in scheduler._next_restart_at

    # The configured limit prevents another automatic restart.
    _fail_service()
    scheduler._handle_post_health("api")

    assert calls == ["api"]
