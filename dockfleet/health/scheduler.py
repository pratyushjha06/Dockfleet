import logging
import threading
import time
from typing import Optional
from sqlmodel import Session, select
from dockfleet.cli.config import DockFleetConfig, HealthCheckConfig
from dockfleet.health.checker import HealthChecker
from dockfleet.health.models import Service, engine
from dockfleet.health.status import (
    update_service_health,
    needs_restart,
    mark_restart_successful,
    record_restart_event,
)
from dockfleet.core.orchestrator import restart_service, mark_restart_failed

DEFAULT_INTERVAL_SECONDS = 30

class HealthScheduler:
    """
    Background scheduler that periodically runs health checks
    for services that have a healthcheck configured in DockFleetConfig.
    - Run HTTP/TCP/process checks via HealthChecker
    - Log results
    - Persist status / last_health_check / restart_count in DB
    - Trigger auto-restart via orchestrator when thresholds are hit
    """

    def __init__(
        self,
        config: DockFleetConfig,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        checker: HealthChecker | None = None,
    ) -> None:
        self.config = config
        self.interval_seconds = interval_seconds

        self._stopped: bool = True
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger(__name__)
        # allow injecting a fake checker in tests, default to real one.
        self._checker: HealthChecker = checker or HealthChecker()

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

        # Reset thread handle so a fresh start() can create a new one
        self._thread = None

    def _poll(self) -> None:
        """
        Main loop to run health checks in the background.

        Uses in-memory DockFleetConfig to know which services to check,
        and writes results into the Service table via update_service_health.
        After each update, evaluates auto-restart rules and, when needed,
        asks the orchestrator to restart the container.
        """
        self._logger.info("HealthScheduler: poll loop started")

        while not self._stopped:
            self._logger.info("HealthScheduler: polling services...")

            for name, svc_cfg in self.config.services.items():
                hc: Optional[HealthCheckConfig] = svc_cfg.healthcheck

                # Skip services without healthcheck
                if hc is None:
                    continue

                try:
                    ok = self._run_single_check(name, hc)
                    status_str = "HEALTHY" if ok else "UNHEALTHY"
                    self._logger.info(
                        "HealthScheduler: %s -> %s", name, status_str
                    )

                    update_service_health(
                        name,
                        ok,
                        reason=None if ok else "health check failed",
                    )

                    # after DB update, decide & trigger restart if needed
                    self._handle_post_health(name)
                except Exception as exc:  # pragma: no cover (defensive)
                    # Defensive guard: one bad service should not kill scheduler
                    self._logger.error(
                        "HealthScheduler: error while polling %s: %s",
                        name,
                        exc,
                    )

            time.sleep(self.interval_seconds)

        self._logger.info("HealthScheduler: poll loop exiting")

    def _handle_post_health(self, name: str) -> None:
        """
        After a health check + DB update for a single service, reload its row,
        evaluate auto-restart rules, and if needed trigger an orchestrator
        restart plus health-side bookkeeping.

        This keeps the decision logic on the health side and the actual
        container restart on the orchestrator side.
        """
        #self-healing toggle from config
        svc_cfg = self.config.services.get(name)
        if svc_cfg is None:
            return

        # Service-level override > top-level default.
        # Assumes DockFleetConfig has `self_healing: bool` and
        # ServiceConfig has `self_healing: bool | None`.
        service_self_healing = getattr(svc_cfg, "self_healing", None)
        if service_self_healing is None:
            self_healing = getattr(self.config, "self_healing", True)
        else:
            self_healing = service_self_healing

        if not self_healing:
            # Auto-restart disabled for this service (or globally)
            self._logger.debug(
                "HealthScheduler: self-healing disabled for %s, "
                "skipping auto-restart",
                name,
            )
            return

        #Load latest DB state
        with Session(engine) as session:
            svc = session.exec(
                select(Service).where(Service.name == name)
            ).one_or_none()

        if svc is None:
            self._logger.warning(
                "HealthScheduler: service '%s' not found in DB after health update",
                name,
            )
            return

        #Check failure threshold + restart policy
        if not needs_restart(svc):
            return

        self._logger.info(
            "HealthScheduler: auto-restart candidate detected: %s "
            "(policy=%s, consecutive_failures=%d)",
            svc.name,
            svc.restart_policy,
            svc.consecutive_failures,
        )

        #Delegate to orchestrator
        try:
            restart_service(svc.name, self.config)

            # On success: reset streak, mark running, and record event.
            mark_restart_successful(svc.name)
            record_restart_event(svc, "3_failed_health_checks")
        except Exception as exc:  # pragma: no cover (defensive)
            # Restart failed: mark as crashed with a readable reason.
            self._logger.error(
                "HealthScheduler: auto-restart failed for %s: %s",
                svc.name,
                exc,
            )
            mark_restart_failed(svc.name, str(exc))

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
