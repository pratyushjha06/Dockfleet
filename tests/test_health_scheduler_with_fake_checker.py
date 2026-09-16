from sqlmodel import Session, select
from dockfleet.health.models import init_db, Service, engine
from dockfleet.health.services import seed_services
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.status import update_service_health
from dockfleet.cli.config import (
    load_config,
    DockFleetConfig,
    HealthCheckConfig,
)


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

    with Session(engine) as session:
        seed_services(config, session)

    service_name = "api"

    fake_checker = FakeChecker(script={service_name: [True, False, False]})

    scheduler = HealthScheduler(
        config=config,
        interval_seconds=1,
        checker=fake_checker,
    )

    def get_service():
        with Session(engine) as session_local:
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
    assert svc.status == "running"

    # Tick 2: unhealthy
    ok2 = scheduler._run_single_check(service_name, hc)
    update_service_health(service_name, ok2, reason="fail 1")
    svc = get_service()
    assert svc.status == "running"
    assert svc.health_status == "crashed"
    assert svc.consecutive_failures == 1

    # Tick 3: unhealthy
    ok3 = scheduler._run_single_check(service_name, hc)
    update_service_health(service_name, ok3, reason="fail 2")
    svc = get_service()
    assert svc.status == "running"
    assert svc.health_status == "crashed"
    assert svc.consecutive_failures == 2