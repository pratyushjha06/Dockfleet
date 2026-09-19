from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from pathlib import Path
from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, HealthCheckConfig
from dockfleet.core.orchestrator import mark_restart_failed, restart_service
from dockfleet.health.checker import HealthChecker
from dockfleet.health.models import ContainerStatus, Service, get_session
from dockfleet.health.scheduler_lock import SchedulerLock
from dockfleet.health.status import (
    mark_restart_successful,
    needs_restart,
    record_restart_event,
    update_service_health,
)

DEFAULT_INTERVAL_SECONDS = 60


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
        project_dir: Path | str | None = None,
    ) -> None:
        self.config = config
        self.interval_seconds = interval_seconds

        self._stopped: bool = True
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)
        # allow injecting a fake checker in tests, default to real one.
        self._checker: HealthChecker = checker or HealthChecker()

        # Runtime state for automatic restart recovery cycles.
        # These values reset when a service becomes healthy again.
        self._restart_attempts: dict[str, int] = {}
        self._next_restart_at: dict[str, float] = {}

        # Cross-process lock to prevent duplicate schedulers for the same
        # project.  Callers must pass ``project_dir`` explicitly to enable
        # locking; ``None`` disables it (e.g. in tests that manage the
        # scheduler manually or in single-pass ``--once`` mode).
        if project_dir is not None:
            self._lock: SchedulerLock | None = SchedulerLock(Path(project_dir))
        else:
            self._lock = None

    def start(self) -> None:
        """
        Start the background polling thread.

        If a ``project_dir`` was provided at construction time, an
        exclusive cross-process lock is acquired first.  If another
        scheduler is already running for the same project a
        :class:`RuntimeError` is raised with an actionable message.

        Raises
        ------
        RuntimeError
            If the cross-process scheduler lock cannot be acquired (another
            instance is already running for this project).
        """
        if self._thread is not None and self._thread.is_alive():
            return

        # Acquire cross-process lock (may raise RuntimeError).
        if self._lock is not None:
            self._lock.acquire()

        self._stopped = False

        self._thread = threading.Thread(
            target=self._poll,
            daemon=True,
            name="HealthSchedulerThread",
        )
        self._thread.start()
        self._logger.info("HealthScheduler: started background thread")

    def stop(self) -> None:
        """
        Signal the polling thread to stop, wait for it to finish, and
        release the cross-process scheduler lock.

        The lock is released only after confirming the thread has exited
        to prevent a new scheduler from starting while the old one is
        still running.
        """
        self._stopped = True

        if self._thread is not None and self._thread.is_alive():
            self._logger.info("HealthScheduler: stopping background thread")
            # Wait for the thread to finish its current loop
            self._thread.join(timeout=self.interval_seconds + 5)

            if self._thread.is_alive():
                # Thread still alive after join timeout — do NOT release the
                # lock; doing so would let a new scheduler start while this
                # one is still running, recreating the duplicate-work race.
                self._logger.warning(
                    "HealthScheduler: thread did not exit within timeout; "
                    "keeping lock to avoid duplicate schedulers"
                )
                return

            self._logger.info("HealthScheduler: thread stopped")

        # Thread is gone (or was never started) — safe to release.
        self._thread = None

        if self._lock is not None and self._lock.is_held:
            self._lock.release()

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

            futures = {}
            # Run all health checks concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                for name, svc_cfg in self.config.services.items():
                    hc: HealthCheckConfig | None = svc_cfg.healthcheck

                    # Skip services without healthcheck
                    if hc is None:
                        continue

                    # Check if the service is currently marked as STOPPED in the database
                    with get_session() as session:
                        svc_db = session.exec(
                            select(Service).where(Service.name == name)
                        ).one_or_none()


                    if svc_db is not None and svc_db.status in (
                        ContainerStatus.STOPPED,
                        ContainerStatus.STOPPED.value,
                    ):
                        self._logger.debug(
                            "HealthScheduler: %s is STOPPED, skipping health check",
                            name,
                        )
                        continue

                    # Submit the check to the thread pool
                    future = executor.submit(self._run_single_check, name, hc)
                    futures[future] = name

                # Process results sequentially to avoid SQLite locking issues
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    try:
                        ok = future.result()
                        status_str = "HEALTHY" if ok else "UNHEALTHY"
                        self._logger.info("HealthScheduler: %s -> %s", name, status_str)

                        if ok:
                            self._restart_attempts.pop(name, None)
                            self._next_restart_at.pop(name, None)

                        update_service_health(
                            name,
                            ok,
                            reason=None if ok else "health check failed",
                        )

                        # after DB update, decide & trigger restart if needed
                        self._handle_post_health(name)
                    except Exception as exc:  # noqa: BLE001 # pragma: no cover (defensive)
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
        # self-healing toggle from config
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

        # Load latest DB state
        with get_session() as session:
            svc = session.exec(
                select(Service).where(Service.name == name)
            ).one_or_none()

        if svc is None:
            self._logger.warning(
                "HealthScheduler: service '%s' not found in DB after health update",
                name,
            )
            return

        # Check failure threshold + restart policy (+ current health state via needs_restart)
        if not needs_restart(svc):
            return

        # Per-service restart limit.
        max_restarts = getattr(svc_cfg, "max_restarts", None)
        restart_attempts = self._restart_attempts.get(name, 0)

        if max_restarts is not None and restart_attempts >= max_restarts:
            self._logger.warning(
                "HealthScheduler: restart limit reached for %s "
                "(attempts=%d, max_restarts=%d)",
                name,
                restart_attempts,
                max_restarts,
            )
            return

        # Exponential backoff before automatic restart.
        backoff_seconds = getattr(svc_cfg, "backoff_seconds", None)
        backoff_multiplier = getattr(svc_cfg, "backoff_multiplier", None)

        if backoff_seconds is not None:
            multiplier = backoff_multiplier if backoff_multiplier is not None else 1.0
            delay = backoff_seconds * (multiplier**restart_attempts)

            now = time.monotonic()
            next_restart_at = self._next_restart_at.get(name)

            if next_restart_at is None:
                self._next_restart_at[name] = now + delay

                if delay > 0:
                    self._logger.info(
                        "HealthScheduler: delaying restart for %s by %.2f seconds",
                        name,
                        delay,
                    )
                    return
            elif now < next_restart_at:
                self._logger.debug(
                    "HealthScheduler: backoff active for %s (%.2f seconds remaining)",
                    name,
                    next_restart_at - now,
                )
                return

        self._logger.info(
            "HealthScheduler: auto-restart candidate detected: %s "
            "(policy=%s, consecutive_failures=%d, health_status=%s)",
            svc.name,
            svc.restart_policy,
            svc.consecutive_failures,
            svc.health_status,
        )

        # Delegate to orchestrator
        try:
            success = restart_service(
                svc.name,
                self.config,
                detailed=True,
            )

            if success is None:
                self._logger.info(
                    "HealthScheduler: restart already in progress for %s",
                    svc.name,
                )
                return

            if not success:
                restart_attempts += 1
                self._restart_attempts[name] = restart_attempts
                self._next_restart_at.pop(name, None)

                self._logger.warning(
                    "HealthScheduler: restart failed for %s " "(attempt=%d)",
                    svc.name,
                    restart_attempts,
                )
                return

            # Count this automatic restart attempt.
            restart_attempts += 1
            self._restart_attempts[name] = restart_attempts
            self._next_restart_at.pop(name, None)

            # On success: reset streak, mark running+healthy, and record event.
            mark_restart_successful(svc.name)
            record_restart_event(svc, "3_failed_health_checks")
        except Exception as exc:  # noqa: BLE001 # pragma: no cover (defensive)
            # Restart failed: mark as crashed with a readable reason.
            self._logger.error(
                "HealthScheduler: auto-restart failed for %s: %s",
                svc.name,
                exc,
            )

            self._restart_attempts[name] = restart_attempts + 1
            self._next_restart_at.pop(name, None)

            mark_restart_failed(svc.name, str(exc))

    def _run_single_check(self, name: str, hc: HealthCheckConfig) -> bool:
        # Run one health check based on its type and return True/False.
        hc_type = hc.type.lower()

        if hc_type in {"http", "tcp"} and hc.endpoint is None:
            self._logger.warning(
                "HealthScheduler: missing %s endpoint for %s",
                hc_type,
                name,
            )
            return False

        if hc_type == "http":
            assert hc.endpoint is not None
            # Expect endpoint like "http://localhost:8000/health"
            return self._checker.check_http(hc.endpoint)

        if hc_type == "tcp":
            assert hc.endpoint is not None
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

    def _split_host_port(self, endpoint: str) -> tuple[str | None, int | None]:
        # Helper to split 'host:port' strings safely.
        if ":" not in endpoint:
            return None, None

        host, port_str = endpoint.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return None, None

        return host, port
