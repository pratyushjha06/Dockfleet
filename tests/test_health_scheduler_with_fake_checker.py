import time
from sqlmodel import select

from dockfleet.cli.config import (
    DockFleetConfig,
    HealthCheckConfig,
    load_config,
)
from dockfleet.health.models import (
    ContainerStatus,
    HealthStatus,
    Service,
    get_session,
    init_db,
)
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.services import seed_services
from dockfleet.health.status import update_service_health


class FakeChecker:
    """
    Tiny fake checker that returns scripted True/False
    values per service name.
    Example:
        script = {"api": [True, False, False]}
    """

    def __init__(self, script: dict[str, list[bool]]):
        # Copy lists so original script dict is not mutated
        self._script = {name: list(seq) for name, seq in script.items()}

    def _next(self, name: str) -> bool:
        seq = self._script.get(name)
        if not seq:
            return True
        if len(seq) == 1:
            return seq[0]
        return seq.pop(0)

    def check_http(self, endpoint: str, timeout: float = 3.0) -> bool:
        return True  # not used in this test

    def check_tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
        return True  # not used in this test

    def check_process(self, container_name: str) -> bool:
        if not container_name.startswith("dockfleet_"):
            return True
        name = container_name.removeprefix("dockfleet_")
        return self._next(name)


def test_scheduler_uses_injected_checker_and_db_updates(tmp_path):
    """
    Smoke test: scheduler uses injected checker and we can
    drive status transitions in DB via scripted results.
    """
    init_db()

    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    with get_session() as session:
        seed_services(config, session)
        for s in session.exec(select(Service)).all():
            s.status = ContainerStatus.RUNNING
            session.add(s)
        session.commit()

    service_name = "api"

    fake_checker = FakeChecker(script={service_name: [True, False, False]})

    scheduler = HealthScheduler(
        config=config,
        interval_seconds=1,
        checker=fake_checker,
    )

    def get_service():
        with get_session() as session_local:
            return session_local.exec(
                select(Service).where(Service.name == service_name)
            ).one()

    # Force healthcheck type to "process" so FakeChecker.check_process is used
    original_hc = config.services[service_name].healthcheck
    assert original_hc is not None
    hc = HealthCheckConfig(
        type="process",
        endpoint=original_hc.endpoint,
        interval=original_hc.interval,
    )
    config.services[service_name].healthcheck = hc

    # Tick 1: healthy
    ok1 = scheduler._run_single_check(service_name, hc)
    update_service_health(service_name, ok1, reason=None)
    svc = get_service()
    assert svc.status == ContainerStatus.RUNNING
    assert svc.health_status == HealthStatus.HEALTHY

    # Tick 2: unhealthy check -> status stays running, health_status becomes unhealthy
    ok2 = scheduler._run_single_check(service_name, hc)
    update_service_health(service_name, ok2, reason="fail 1")
    svc = get_service()
    assert svc.status == ContainerStatus.RUNNING
    assert svc.health_status == HealthStatus.UNHEALTHY
    assert svc.consecutive_failures == 1

    # Tick 3: unhealthy check
    ok3 = scheduler._run_single_check(service_name, hc)
    update_service_health(service_name, ok3, reason="fail 2")
    svc = get_service()
    assert svc.status == ContainerStatus.RUNNING
    assert svc.health_status == HealthStatus.UNHEALTHY
    assert svc.consecutive_failures == 2

    # Tick 4: 3rd unhealthy check -> reaches threshold for CRASHED
    ok4 = scheduler._run_single_check(service_name, hc)
    update_service_health(service_name, ok4, reason="fail 3")
    svc = get_service()
    assert svc.status == ContainerStatus.RUNNING
    assert svc.health_status == HealthStatus.CRASHED
    assert svc.consecutive_failures == 3


def test_scheduler_skips_stopped_service():
    init_db()

    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    with get_session() as session:
        seed_services(config, session)
        for s in session.exec(select(Service)).all():
            s.status = ContainerStatus.STOPPED
            s.consecutive_failures = 0
            session.add(s)
        session.commit()

    called = []

    class TrackingChecker:
        def check_process(self, name):
            called.append(name)
            return False

        def check_http(self, *args, **kwargs):
            called.append("http")
            return False

        def check_tcp(self, *args, **kwargs):
            called.append("tcp")
            return False

    scheduler = HealthScheduler(
        config=config,
        interval_seconds=1,
        checker=TrackingChecker(),
    )
    scheduler.start()
    time.sleep(0.3)
    scheduler.stop()

    assert len(called) == 0

    with get_session() as session:
        for s in session.exec(select(Service)).all():
            assert s.status == ContainerStatus.STOPPED
            assert s.consecutive_failures == 0


def test_scheduler_run_single_pass():
    init_db()

    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    with get_session() as session:
        seed_services(config, session)
        svc = session.exec(select(Service).where(Service.name == "api")).one()
        svc.status = ContainerStatus.RUNNING
        session.add(svc)
        session.commit()

    original_hc = config.services["api"].healthcheck
    assert original_hc is not None
    hc = HealthCheckConfig(
        type="process",
        endpoint=original_hc.endpoint,
        interval=original_hc.interval,
    )
    config.services["api"].healthcheck = hc

    fake_checker = FakeChecker(script={"api": [True]})
    scheduler = HealthScheduler(
        config=config,
        checker=fake_checker,
    )

    results = scheduler.run_single_pass()
    assert "api" in results
    assert results["api"] is True

    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == "api")).one()
        assert svc.status == ContainerStatus.RUNNING
        assert svc.health_status == HealthStatus.HEALTHY
        assert svc.consecutive_failures == 0



