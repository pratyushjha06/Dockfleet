"""Tests for per-service healthcheck intervals.

The scheduler used to run every check on the same fixed loop interval and
ignored ``healthcheck.interval``.  These tests pin the deadline bookkeeping,
the due-service selection and the poll loop cadence with a fake clock.
"""

from types import SimpleNamespace

from dockfleet.cli.config import (
    DockFleetConfig,
    HealthCheckConfig,
    RestartPolicy,
    ServiceConfig,
)
from dockfleet.health.scheduler import HealthScheduler


class FakeClock:
    """Deterministic monotonic clock that advances when sleep is called."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _config(intervals: dict[str, tuple[int, int]]) -> DockFleetConfig:
    services = {
        name: ServiceConfig(
            image="nginx:alpine",
            restart=RestartPolicy.always,
            healthcheck=HealthCheckConfig(
                type="http",
                endpoint=f"http://localhost:{port}/health",
                interval=interval,
            ),
        )
        for name, (interval, port) in intervals.items()
    }
    return DockFleetConfig(services=services)


def _scheduler(config, interval_seconds: int = 60) -> HealthScheduler:
    return HealthScheduler(config=config, interval_seconds=interval_seconds)


def _stub_pass(monkeypatch, scheduler) -> None:
    monkeypatch.setattr(scheduler, "_run_single_check", lambda name, hc: True)
    monkeypatch.setattr(
        "dockfleet.health.scheduler.update_service_health", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(scheduler, "_handle_post_health", lambda name: None)


def test_run_single_pass_records_each_service_deadline(monkeypatch):
    config = _config({"fast": (5, 8000), "slow": (60, 8001)})
    scheduler = _scheduler(config)
    clock = FakeClock()

    monkeypatch.setattr("time.monotonic", clock.monotonic)
    _stub_pass(monkeypatch, scheduler)

    results = scheduler.run_single_pass()

    assert results == {"fast": True, "slow": True}
    assert scheduler._next_check_at == {"fast": 1005.0, "slow": 1060.0}


def test_programmatic_healthcheck_without_interval_uses_loop_interval(monkeypatch):
    service = SimpleNamespace(
        healthcheck=SimpleNamespace(type="process", endpoint=None, interval=None)
    )
    config = SimpleNamespace(services={"api": service})
    scheduler = HealthScheduler(config=config, interval_seconds=30)
    clock = FakeClock()

    monkeypatch.setattr("time.monotonic", clock.monotonic)
    _stub_pass(monkeypatch, scheduler)

    scheduler.run_single_pass()

    assert scheduler._next_check_at == {"api": 1030.0}


def test_due_services_returns_only_passed_deadlines():
    scheduler = _scheduler(_config({"fast": (5, 8000), "slow": (60, 8001)}))
    scheduler._next_check_at = {"fast": 1005.0, "slow": 1060.0}

    assert scheduler._due_services(1004.9) == []
    assert scheduler._due_services(1005.0) == ["fast"]
    assert scheduler._due_services(1060.0) == ["fast", "slow"]


def test_next_sleep_seconds_waits_for_the_earliest_deadline(monkeypatch):
    scheduler = _scheduler(
        _config({"fast": (5, 8000), "slow": (60, 8001)}), interval_seconds=10
    )
    scheduler._next_check_at = {"fast": 1005.0, "slow": 1060.0}

    monkeypatch.setattr("time.monotonic", lambda: 1002.0)

    assert scheduler._next_sleep_seconds(elapsed=5.0) == 3.0


def test_next_sleep_seconds_caps_long_intervals(monkeypatch):
    scheduler = _scheduler(_config({"slow": (600, 8000)}), interval_seconds=10)
    scheduler._next_check_at = {"slow": 1900.0}

    monkeypatch.setattr("time.monotonic", lambda: 1000.0)

    assert scheduler._next_sleep_seconds(elapsed=1.0) == 10.0


def test_next_sleep_seconds_keeps_fixed_interval_fallback():
    scheduler = _scheduler(_config({}), interval_seconds=10)

    assert scheduler._next_sleep_seconds(elapsed=2.5) == 7.5


def test_poll_runs_each_service_on_its_own_interval(monkeypatch):
    config = _config({"fast": (5, 8000), "slow": (60, 8001)})
    scheduler = _scheduler(config, interval_seconds=60)
    clock = FakeClock()
    calls: list[list[str]] = []

    def fake_pass(services=None):
        names = list(config.services) if services is None else list(services)
        calls.append(names)
        for name in names:
            interval = config.services[name].healthcheck.interval
            scheduler._next_check_at[name] = clock.now + interval
        return {name: True for name in names}

    def sleep_and_stop(seconds: float) -> None:
        clock.sleep(seconds)
        if len(clock.sleeps) == 3:
            scheduler._stopped = True

    monkeypatch.setattr("time.monotonic", clock.monotonic)
    monkeypatch.setattr("time.sleep", sleep_and_stop)
    monkeypatch.setattr(scheduler, "run_single_pass", fake_pass)

    scheduler._stopped = False
    scheduler._poll()

    assert calls == [["fast", "slow"], ["fast"], ["fast"]]
    assert clock.sleeps == [5.0, 5.0, 5.0]
