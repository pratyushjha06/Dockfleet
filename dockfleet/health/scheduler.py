import logging
import threading
import time
from typing import Optional
from dockfleet.cli.config import DockFleetConfig, HealthCheckConfig
from dockfleet.health.checker import HealthChecker

DEFAULT_INTERVAL_SECONDS = 30

class HealthScheduler:
    """
    Background scheduler to periodically runs health checks
    for services that have a healthcheck configured in DockFleetConfig.

    Day 8 scope: only logs/prints healthy/unhealthy, no DB writes.
    """
    def __init__(
        self,
        config: DockFleetConfig,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self.config = config
        self.interval_seconds = interval_seconds

        self._stopped: bool = True
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger(__name__)
        self._checker = HealthChecker()

    def start(self) -> None:
        # Start the background polling thread if it's not already running.
        if self._thread is not None and self._thread.is_alive():
            return

        self._stopped = False

        self._thread = threading.Thread(
            target=self._poll,
            daemon=True,
            name="HealthSchedulerThread",
        )
        self._thread.start()
        self._logger.info("HealthScheduler: started background thread")

    def stop(self) -> None:
        # Signal the polling thread to stop and wait for it to finish.

        self._stopped = True

        if self._thread is not None and self._thread.is_alive():
            self._logger.info("HealthScheduler: stopping background thread")
            # Wait for the thread to finish its current loop
            self._thread.join(timeout=self.interval_seconds + 5)
            self._logger.info("HealthScheduler: thread stopped")

    def _poll(self) -> None:
        """
        Main loop to runs health checks in the background.

        Day 8: read from in-memory config only, no DB writes.
        """
        self._logger.info("HealthScheduler: poll loop started")

        while not self._stopped:
            self._logger.info("HealthScheduler: polling services...")

            for name, svc_cfg in self.config.services.items():
                hc: Optional[HealthCheckConfig] = svc_cfg.healthcheck

                # Skip services without healthcheck
                if hc is None:
                    continue

                ok = self._run_single_check(name, hc)
                status_str = "HEALTHY" if ok else "UNHEALTHY"
                self._logger.info("HealthScheduler: %s -> %s", name, status_str)

            time.sleep(self.interval_seconds)

        self._logger.info("HealthScheduler: poll loop exiting")

    def _run_single_check(self, name: str, hc: HealthCheckConfig) -> bool:
        # Run one health check based on its type and return True/False.
 
        hc_type = hc.type.lower()

        if hc_type == "http":
            # Expect endpoint like "http://localhost:8000/health"
            return self._checker.check_http(hc.endpoint)

        if hc_type == "tcp":
            # Expect endpoint like "localhost:8000"
            host, port = self._split_host_port(hc.endpoint)
            if host is None or port is None:
                self._logger.warning(
                    "HealthScheduler: invalid TCP endpoint for %s: %s",
                    name,
                    hc.endpoint,
                )
                return False
            return self._checker.check_tcp(host, port)

        if hc_type == "process":
            # Convention: Docker container names use "dockfleet_{service_name}"
            container_name = f"dockfleet_{name}"
            return self._checker.check_process(container_name)

        self._logger.warning(
            "HealthScheduler: unknown healthcheck type for %s: %s",
            name,
            hc.type,
        )
        return False

    def _split_host_port(self, endpoint: str) -> tuple[Optional[str], Optional[int]]:
        # Helper to split 'host:port' strings safely.

        if ":" not in endpoint:
            return None, None

        host, port_str = endpoint.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return None, None

        return host, port
