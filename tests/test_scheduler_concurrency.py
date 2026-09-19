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
