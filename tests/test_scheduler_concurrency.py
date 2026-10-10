import time

from dockfleet.cli.config import DockFleetConfig, HealthCheckConfig, ServiceConfig
from dockfleet.health.models import get_session, init_db
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.services import seed_services


class SlowFakeChecker:
    def __init__(self):
        self._real_sleep = time.sleep

    def check_http(self, endpoint: str, timeout: float = 3.0) -> bool:
        self._real_sleep(1.0)
        return True

    def check_tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
        self._real_sleep(1.0)
        return True

    def check_process(self, container_name: str) -> bool:
        self._real_sleep(1.0)
        return True

def test_scheduler_runs_concurrently(monkeypatch):
    """
    Ensure that health checks for multiple services are executed concurrently.
    If executed sequentially, 3 services taking 1 second each would take 3+ seconds.
    If concurrent, it should take ~1 second.
    """
    init_db()
    services = {
        f"service_{i}": ServiceConfig(
            name=f"service_{i}",
            image="nginx",
            restart="always",
            healthcheck=HealthCheckConfig(type="http", endpoint="http://localhost", interval=10)
        ) for i in range(3)
    }
    config = DockFleetConfig(services=services)
    
    with get_session() as session:
        seed_services(config, session)
    
    # We monkeypatch time.sleep to avoid actual waiting in _poll if it reaches the end of the loop
    def mock_sleep(seconds):
        pass
    monkeypatch.setattr(time, "sleep", mock_sleep)
    
    checker = SlowFakeChecker()
    scheduler = HealthScheduler(config=config, interval_seconds=1, checker=checker)
    
    start_time = time.time()
    
    # Instead of running start(), we manually invoke the inside of the while loop once.
    # To do this safely, we will let _poll run but exit after one loop.
    scheduler._stopped = False
    
    def mock_sleep_stop(seconds):
        scheduler._stopped = True
        
    monkeypatch.setattr(time, "sleep", mock_sleep_stop)
    
    scheduler._poll()
    
    duration = time.time() - start_time
    
    # It should take roughly 1 second for all 3 checks, definitely less than 2.5 seconds
    assert duration < 2.5, f"Expected concurrent execution taking ~1s, took {duration}s"


def test_scheduler_records_all_services_before_autorestart(monkeypatch):
    """
    Ensure that when a service triggers an auto-restart, health check results for
    all other services are already recorded immediately in SQLite.
    """
    from sqlmodel import select
    from dockfleet.health.models import ContainerStatus, HealthStatus, Service

    init_db()
    services = {
        f"service_{i}": ServiceConfig(
            name=f"service_{i}",
            image="nginx",
            restart="always",
            healthcheck=HealthCheckConfig(type="http", endpoint="http://localhost", interval=10)
        ) for i in range(1, 6)
    }
    config = DockFleetConfig(services=services)

    with get_session() as session:
        seed_services(config, session)
        for i in range(1, 6):
            svc = session.exec(select(Service).where(Service.name == f"service_{i}")).one()
            svc.status = ContainerStatus.RUNNING
            if i == 1:
                # Pre-set service_1 to 2 consecutive failures so next failure triggers auto-restart
                svc.consecutive_failures = 2
                svc.health_status = HealthStatus.UNHEALTHY
            session.add(svc)
        session.commit()

    class CustomChecker:
        def check_http(self, endpoint: str, timeout: float = 3.0) -> bool:
            return True
        def check_tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
            return True
        def check_process(self, container_name: str) -> bool:
            return True

    checker = CustomChecker()
    # service_1 fails, services 2-5 succeed
    def mock_run_single_check(name, hc):
        if name == "service_1":
            return False
        return True

    recorded_statuses_during_restart = {}

    def mock_restart_service(name, cfg, detailed=False):
        # Verify that services 2-5 have already been recorded in SQLite when restart is triggered
        with get_session() as session:
            for i in range(2, 6):
                svc = session.exec(select(Service).where(Service.name == f"service_{i}")).one()
                recorded_statuses_during_restart[svc.name] = (svc.health_status, svc.last_health_check)
        return True

    monkeypatch.setattr("dockfleet.health.scheduler.restart_service", mock_restart_service)

    scheduler = HealthScheduler(config=config, interval_seconds=1, checker=checker)
    monkeypatch.setattr(scheduler, "_run_single_check", mock_run_single_check)

    results = scheduler.run_single_pass()

    assert len(results) == 5
    assert results["service_1"] is False
    assert all(results[f"service_{i}"] is True for i in range(2, 6))

    # Assert services 2-5 had their health check results recorded in SQLite BEFORE restart_service executed
    for i in range(2, 6):
        svc_name = f"service_{i}"
        assert svc_name in recorded_statuses_during_restart
        health_status, last_check = recorded_statuses_during_restart[svc_name]
        assert health_status == HealthStatus.HEALTHY
        assert last_check is not None

