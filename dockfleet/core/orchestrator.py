import logging
import re
import socket
import subprocess
import sys
import threading
import time

from pydantic import BaseModel
from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, RestartPolicy
from dockfleet.core.docker import DockerManager
from dockfleet.core.docker_flags import (
    build_env_flags,
    build_port_flags,
    build_resource_flags,
)
from dockfleet.health.logs import store_log_line
from dockfleet.health.models import (
    ContainerStatus,
    HealthStatus,
    Service,
    get_session,
)
from dockfleet.health.seed import bootstrap_from_config
from dockfleet.health.status import (
    mark_restart_successful,
    mark_service_running,
    mark_service_stopped,
    record_restart_event,
)

logger = logging.getLogger(__name__)

_UNSET = object()


class ServiceStat(BaseModel):
    """
    Real-time performance and container metrics for a managed service.
    """

    service_name: str
    container_name: str
    cpu_percent: float | None = None
    mem_current: str | None = None
    mem_percent: str | None = None
    uptime: str | None = None
    status: ContainerStatus | str = ContainerStatus.UNKNOWN  # running, stopped, missing


_orchestrator_instance = None
_orchestrator_lock = threading.Lock()


def get_container_name(service_name: str) -> str:
    """Shared container name helper for logs."""
    return f"dockfleet_{service_name}"


def get_service_stats(config=None):
    """Module wrapper for stats."""
    orch = get_orchestrator(config)
    return orch.get_service_stats()


def get_orchestrator(config=None, self_healing=_UNSET):
    """Return the module-level Orchestrator singleton, creating it on first call.

    The singleton is created once and reused for the lifetime of the process.
    If a subsequent call passes a *different* ``config`` or ``self_healing``
    value, a warning is logged and the original instance is returned unchanged.

    ``self_healing`` uses a sentinel default so that callers which omit the
    argument (e.g. ``restart_service()``, ``/settings``) do not spuriously
    trigger a mismatch warning when the singleton was created with
    ``self_healing=False``.

    Thread-safe: the entire read-or-create operation is atomic under a single
    lock, so concurrent calls (including concurrent ``reset_orchestrator()``)
    never observe a stale or partially-created instance.

    Use :func:`reset_orchestrator` to explicitly clear the singleton (intended
    for tests and deliberate full-reconfiguration flows, not production code).
    """
    global _orchestrator_instance

    # Resolve sentinel to the real default before any comparison or creation.
    resolved_self_healing = True if self_healing is _UNSET else self_healing

    with _orchestrator_lock:
        if _orchestrator_instance is not None:
            _warn_on_mismatch(_orchestrator_instance, config, self_healing)
            return _orchestrator_instance

        # Default to a proper DockFleetConfig (not a bare dict) so
        # Orchestrator.__init__ can safely assign .services on it.
        effective_config = config or DockFleetConfig(services={})
        _orchestrator_instance = Orchestrator(
            effective_config, self_healing=resolved_self_healing
        )
        return _orchestrator_instance


def _warn_on_mismatch(orch, config, self_healing):
    """Log a warning if caller-supplied args differ from the live singleton.

    When ``self_healing`` is the sentinel ``_UNSET`` (caller didn't pass it),
    the self_healing comparison is skipped entirely — only ``config`` changes
    are reported.  This prevents spurious warnings for callers like
    ``restart_service()`` and ``/settings`` that never pass ``self_healing``.
    """
    changes = []

    if config is not None and config != orch.config:
        changes.append("config")
    if self_healing is not _UNSET and self_healing != orch.self_healing:
        changes.append("self_healing")

    if changes:
        logger.warning(
            "get_orchestrator() called with changed %s but orchestrator "
            "singleton already exists — arguments ignored. Existing values: "
            "self_healing=%s. Call reset_orchestrator() first if you need "
            "a fresh instance.",
            ", ".join(changes),
            orch.self_healing,
        )


def reset_orchestrator():
    """
    Safe to call even if no singleton has been created yet (no-op).

    Thread-safe: holds ``_orchestrator_lock`` for the entire operation so
    concurrent ``get_orchestrator()`` calls never observe a stale instance.
    """
    global _orchestrator_instance

    with _orchestrator_lock:
        _orchestrator_instance = None


def restart_service(
    name: str,
    config=None,
    detailed: bool = False,
) -> bool | None:
    """Module wrapper for HealthScheduler."""
    orch = get_orchestrator(config)
    return orch.restart_service(name, config, detailed=detailed)


def mark_restart_failed(name: str, reason: str) -> None:
    """Module wrapper for HealthScheduler."""
    orch = get_orchestrator()
    orch._mark_restart_failed(name, reason)


def get_logs(
    service_name: str,
    lines: int = 100,
    follow: bool = False,
    persist: bool = False,
):
    """
    Docker logs wrapper for SSE layer with optional persistence.

    - If persist=True, sampled log lines are also written to LogEvent via
      health.logs.store_log_line for later search/analytics.
    """
    container_name = get_container_name(service_name)

    cmd = ["docker", "logs", container_name, "--tail", str(lines)]
    if follow:
        cmd.append("-f")

    process = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        for line in iter(process.stdout.readline, ""):
            clean_line = line.strip()
            if not clean_line:
                continue

            yield clean_line

            if persist:
                try:
                    store_log_line(
                        service_name,
                        clean_line,
                        level=None,
                        source="docker-logs",
                    )
                except Exception as e:
                    logger.warning("log store failed for %s: %s", service_name, e)

    except Exception as e:
        logger.error("Failed to stream logs for %s: %s", container_name, e)
        yield f"Error: {e}"
    finally:
        if process is not None:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception as e:
                    logger.warning("Error closing stdout for %s: %s", container_name, e)

            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except Exception:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
            except Exception as e:
                logger.warning(
                    "Error cleaning up log process for %s: %s", container_name, e
                )



def normalize_services(services):
    """
    Normalize list of service configs into a dictionary keyed by service name.
    """
    if isinstance(services, list):
        normalized = {}
        for svc in services:
            name = svc.get("name")
            if not name:
                raise ValueError("Service missing 'name'")
            normalized[name] = svc
        return normalized
    return services or {}


def _extract_host_ports(service_config: dict) -> list[tuple[str, int]]:
    """Extract list of (host, port) tuples configured for a service (only explicit host port bindings)."""
    ports = service_config.get("ports") or []
    if isinstance(ports, dict):
        raw_ports = [f"{k}:{v}" for k, v in ports.items()]
    elif isinstance(ports, list):
        raw_ports = ports
    else:
        raw_ports = [ports]

    host_ports: list[tuple[str, int]] = []
    for p in raw_ports:
        p_str = str(p).strip()
        if not p_str:
            continue
        if "/" in p_str:
            p_str = p_str.split("/")[0]
        parts = p_str.split(":")
        try:
            if len(parts) == 1:
                # Single container port (e.g. "80", 80). No explicit host port binding.
                continue
            elif len(parts) == 2:
                if "." in parts[0]:
                    # IP with container port (no explicit host port, e.g. "127.0.0.1:80")
                    continue
                else:
                    host_ports.append(("0.0.0.0", int(parts[0])))
            elif len(parts) == 3:
                host_ip = parts[0] if parts[0] else "0.0.0.0"
                if parts[1]:
                    host_ports.append((host_ip, int(parts[1])))
        except (ValueError, TypeError):
            continue
    return host_ports


def is_port_released(port: int, host: str = "0.0.0.0") -> bool:
    """Verify that a socket binding on the host port is released and available."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if sys.platform == "win32":
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                except (AttributeError, OSError):
                    pass
            s.bind((host, port))
            return True
    except OSError:
        return False


def wait_for_ports_released(
    ports: list[tuple[str, int] | int],
    timeout: float = 5.0,
    poll_interval: float = 0.05,
) -> bool:
    """Poll until all specified host ports are released, or until timeout."""
    if not ports:
        return True

    def _check_binding(p: tuple[str, int] | int) -> bool:
        if isinstance(p, tuple):
            host, port = p
            return is_port_released(port, host=host)
        return is_port_released(p)

    start = time.time()
    while time.time() - start < timeout:
        if all(_check_binding(p) for p in ports):
            return True
        time.sleep(poll_interval)
    return all(_check_binding(p) for p in ports)


class Orchestrator:
    """
    Main orchestration engine managing container lifecycle, deployments, and self-healing restarts.
    """

    def __init__(self, config, self_healing: bool = True):
        """Initialize the Orchestrator with configuration and self-healing toggle."""
        self.config = config
        self.config.services = normalize_services(getattr(config, "services", {}))
        self.self_healing = self_healing
        self.docker = DockerManager()
        self.network = "dockfleet_net"
        self._active_restarts: set[str] = set()
        self._restart_lock = threading.Lock()

    def container_name(self, service: str) -> str:
        """Return the standard Docker container name for a service."""
        return f"dockfleet_{service}"

    def start_service(self, name, svc) -> bool:
        """
        Start a container for the given service definition and update database status.
        Returns True if successful, False otherwise.
        """
        container_name = self.container_name(name)

        try:
            # Best-effort remove any previous container
            try:
                self.docker.remove_container(container_name)
            except Exception:
                logger.debug("No existing container to remove for %s", container_name)

            # Convert to dict safely
            if isinstance(svc, dict):
                service_config = svc
            elif hasattr(svc, "model_dump"):
                service_config = svc.model_dump()
            else:
                service_config = vars(svc)

            # VALIDATION
            if not service_config.get("image"):
                raise ValueError(f"Service '{name}' missing 'image'")

            # DEFAULTS
            service_config["ports"] = service_config.get("ports") or []

            # FIX ports (dict → list)
            if isinstance(service_config["ports"], dict):
                service_config["ports"] = [
                    f"{k}:{v}" for k, v in service_config["ports"].items()
                ]

            # Build flags safely
            port_flags = build_port_flags(service_config)
            env_flags = build_env_flags(service_config)
            resource_flags = build_resource_flags(service_config)

            docker_flags = port_flags + env_flags + resource_flags

            # Verify that existing socket bindings are fully released before container spin-up
            host_ports = _extract_host_ports(service_config)
            if host_ports:
                wait_for_ports_released(host_ports, timeout=5.0)

            # Always use service_config (not svc)
            self.docker.run_container(
                image=service_config.get("image"),
                name=container_name,
                flags=docker_flags,
                network=self.network,
            )

            mark_service_running(name)
            logger.info("Started service: %s", name)
            return True

        except Exception as e:
            logger.error("Failed to start %s: %s", name, e)
            return False

    def stop_service(self, name) -> bool:
        """Stop and remove a container for the given service, marking status STOPPED."""
        container_name = self.container_name(name)

        try:
            # Best-effort stop (ignore absent container errors)
            try:
                self.docker.stop_container(container_name)
            except Exception as e:
                if "No such" not in str(e) and "not found" not in str(e).lower():
                    raise e

            # Best-effort remove
            try:
                self.docker.remove_container(container_name)
            except Exception as e:
                if "No such" not in str(e) and "not found" not in str(e).lower():
                    raise e

            mark_service_stopped(name)
            logger.info("Stopped service: %s", name)
            return True

        except Exception as e:
            logger.error("Failed to stop %s: %s", name, e)
            return False

    def _mark_restart_failed(self, service_name: str, reason: str) -> None:
        """Mark a service restart attempt as failed in DB, setting status=STOPPED and health_status=CRASHED."""
        try:
            with get_session() as session:
                db_svc = session.exec(
                    select(Service).where(Service.name == service_name)
                ).one_or_none()
                if db_svc:
                    db_svc.status = ContainerStatus.STOPPED
                    db_svc.health_status = HealthStatus.CRASHED
                    db_svc.last_failure_reason = f"auto-restart failed: {reason}"
                    session.add(db_svc)
                    session.commit()
                    logger.warning(
                        "Marked restart failed for %s: %s", service_name, reason
                    )
        except Exception as exc:
            logger.error(
                "Failed to update DB for failed restart %s: %s", service_name, exc
            )

    def restart_service(
        self,
        service_name: str,
        config=None,
        backoff_attempt: int = 0,
        detailed: bool = False,
    ) -> bool | None:
        """
        Restart a service's container, respecting restart_policy and self_healing.

        - Guards against concurrent restarts for the same service.
        - Sets DB health_status = HealthStatus.RESTARTING during execution.
        - If restart_policy == "never": do nothing and return False.
        - Otherwise:
          - Best-effort stop of any existing container.
          - Start a fresh container with the same config.
          - Return True if the new container start succeeded.
        """
        config = config or self.config

        if not self.self_healing:
            logger.info("Self-healing disabled, skip restart for %s", service_name)
            return False

        if service_name not in config.services:
            logger.warning("Service %s not found", service_name)
            return False

        svc = config.services[service_name]

        # Respect restart_policy == never
        if getattr(svc, "restart", None) == RestartPolicy.never:
            logger.info("%s: restart='never', skipping", service_name)
            return False

        # Concurrency guard: thread check
        with self._restart_lock:
            if service_name in self._active_restarts:
                logger.warning(
                    "Restart already in progress for service %s", service_name
                )
                return None if detailed else False
            self._active_restarts.add(service_name)

        try:
            # Set DB health_status to RESTARTING during restart execution
            with get_session() as session:
                db_svc = session.exec(
                    select(Service).where(Service.name == service_name)
                ).one_or_none()
                if db_svc:
                    db_svc.health_status = HealthStatus.RESTARTING
                    session.add(db_svc)
                    session.commit()

            logger.info("Restarting %s", service_name)
            container_name = self.container_name(service_name)

            # Best-effort stop; even if this fails, we still try to start a new one
            try:
                subprocess.run(
                    ["docker", "stop", container_name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception as exc:
                logger.warning(
                    "restart_service: error stopping %s: %s", container_name, exc
                )

            # Ensure container is removed and socket bindings are released
            try:
                self.docker.remove_container(container_name)
            except Exception as exc:
                logger.debug("restart_service: remove_container %s: %s", container_name, exc)

            host_ports = _extract_host_ports(
                svc
                if isinstance(svc, dict)
                else (
                    svc.model_dump()
                    if hasattr(svc, "model_dump")
                    else vars(svc)
                )
            )
            if host_ports:
                wait_for_ports_released(host_ports, timeout=5.0)

            # Try to start a fresh container
            try:
                if self.start_service(service_name, svc) is False:
                    self._mark_restart_failed(
                        service_name, "start_service returned False"
                    )
                    return False

                self._increment_restart_count(service_name)
                logger.info("%s restarted (count updated)", service_name)
                return True
            except Exception as e:
                logger.error("%s restart FAILED: %s", service_name, e)
                self._mark_restart_failed(service_name, str(e))
                return False
        except Exception as e:
            try:
                with get_session() as session:
                    db_svc = session.exec(
                        select(Service).where(Service.name == service_name)
                    ).one_or_none()
                    if db_svc and db_svc.health_status == HealthStatus.RESTARTING:
                        db_svc.health_status = HealthStatus.CRASHED
                        session.add(db_svc)
                        session.commit()
            except Exception:
                pass
            raise e
        finally:
            with self._restart_lock:
                self._active_restarts.discard(service_name)

    def _increment_restart_count(self, service_name: str) -> None:
        """Increment the cumulative restart count for a service in the database."""
        try:
            with get_session() as session:
                svc = session.exec(
                    select(Service).where(Service.name == service_name)
                ).one_or_none()

                if svc:
                    svc.restart_count = (svc.restart_count or 0) + 1
                    session.add(svc)
                    session.commit()
                    logger.info(
                        "DB: %s restart_count=%s", service_name, svc.restart_count
                    )
                else:
                    logger.warning("Service %s not in DB", service_name)

        except Exception as e:
            logger.error("DB increment failed for %s: %s", service_name, e)

    def monitor_services(self):
        """
        Legacy monitor; not used by dockfleet up anymore.
        HealthScheduler is now responsible for continuous monitoring.
        """
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                capture_output=True,
                text=True,
            )

            for line in result.stdout.splitlines():
                if not line.strip():
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                name, status = parts[0], parts[1]

                if not name.startswith("dockfleet_"):
                    continue

                service_name = name.removeprefix("dockfleet_")

                if "Exited" in status:
                    logger.warning("%s detected as crashed (%s)", service_name, status)

                    self.handle_unhealthy_service(
                        service_name,
                        reason="health failure",
                    )

        except Exception as e:
            logger.error("Monitor failed: %s", e)

    def handle_unhealthy_service(
        self,
        service_name: str,
        config=None,
        reason: str = "health failure",
    ) -> None:
        """
        Handle an unhealthy service by performing self-healing auto-restart and logging restart events.
        """
        config = config or self.config
        logger.info("Auto-restart: %s (%s)", service_name, reason)

        if not self.self_healing:
            logger.info("Self-healing disabled, skip auto-restart for %s", service_name)
            return

        try:
            success = self.restart_service(service_name)
        except Exception as exc:
            logger.error("CRITICAL restart error %s: %s", service_name, exc)
            self._mark_restart_failed(service_name, str(exc))
            return

        if not success:
            logger.info(
                "restart_service skipped or already in progress for %s", service_name
            )
            return

        logger.info("%s auto-restarted", service_name)
        mark_restart_successful(service_name)

        with get_session() as session:
            svc = session.exec(
                select(Service).where(Service.name == service_name)
            ).one_or_none()
            if svc:
                record_restart_event(svc, reason)
            else:
                logger.warning("Service %s not found after restart", service_name)

    def _resolve_service_order(self):
        """Topologically sort services based on depends_on configuration."""
        visited = set()
        visiting = set()
        order = []

        def visit(name):
            if name in visiting:
                raise ValueError(f"Circular dependency detected: {name}")

            if name in visited:
                return

            visiting.add(name)

            svc = self.config.services[name]

            if isinstance(svc, dict):
                deps = svc.get("depends_on", [])
            else:
                deps = getattr(svc, "depends_on", []) or []

            for dep in deps:
                if dep in self.config.services:
                    visit(dep)

            visiting.remove(name)
            visited.add(name)
            order.append(name)

        for name in self.config.services:
            visit(name)

        return order

    def up(self):
        """
        Start all services once and return. Raises an exception if any services fail to start.

        Continuous monitoring and self-healing are handled by HealthScheduler;
        this method should not block.
        """
        print("Starting services...\n")

        # Ensure DB has Service rows for this config
        bootstrap_from_config(self.config)
        print(" DB bootstrapped & services seeded")

        # Create network (idempotent)
        self.docker.create_network(self.network)

        # Start services in dependency order
        order = self._resolve_service_order()
        failed = []

        for name in order:
            svc = self.config.services[name]
            success = self.start_service(name, svc)
            if not success:
                failed.append(name)

        if failed:
            raise RuntimeError(f"Failed to start services: {failed}")

        print("All services started.")

    def down(self):
        """Stop and remove all services in reverse dependency order. Raises an exception if any service fails to stop."""
        print("Stopping services...\n")
        failed = []

        order = list(reversed(self._resolve_service_order()))
        for name in order:
            success = self.stop_service(name)
            if not success:
                failed.append(name)
        if failed:
            raise RuntimeError(f"Failed to stop services: {failed}")

    def get_ps_data(self) -> list[dict]:

        raw_containers = self.docker.get_containers_json()

        db_health_map = {}
        try:
            with get_session() as session:
                db_services = session.exec(select(Service)).all()
                for svc in db_services:
                    hs = getattr(svc, "health_status", None)
                    if isinstance(hs, HealthStatus):
                        db_health_map[svc.name] = hs.value
                    elif hs:
                        db_health_map[svc.name] = str(hs).lower()
        except Exception as exc:
            logger.debug("Failed to fetch service health from DB in get_ps_data: %s", exc)

        results = []
        for container in raw_containers:
            name = container.get("Names") or container.get("Name") or ""
            if isinstance(name, list):
                name = name[0] if name else ""
            if not isinstance(name, str) or not name.startswith("dockfleet_"):
                continue
            service_name = name.removeprefix("dockfleet_")

            status_raw = container.get("Status", "")
            state_raw = container.get("State", "")

            if "Up" in status_raw or state_raw == "running":
                status = "running"
            elif "Restarting" in status_raw or state_raw == "restarting":
                status = "restarting"
            elif "Exited" in status_raw or state_raw == "exited":
                status = "stopped"
            else:
                status = state_raw if state_raw else "unknown"

            db_health = db_health_map.get(service_name)

            if db_health in (
                HealthStatus.UNHEALTHY.value,
                HealthStatus.CRASHED.value,
                HealthStatus.RESTARTING.value,
            ):
                health = db_health
            elif "(unhealthy)" in status_raw:
                health = "unhealthy"
            elif "(healthy)" in status_raw:
                health = "healthy"
            elif "(health: starting)" in status_raw:
                health = "starting"
            elif db_health:
                health = db_health
            elif status == "running":
                health = "healthy"
            elif status == "stopped":
                health = "stopped"
            else:
                health = "unknown"

            results.append(
                {
                    "name": service_name,
                    "status": status,
                    "health": health,
                }
            )

        return results

    def ps(self, json_output: bool = False):
        """List currently running containers managed by DockFleet."""
        if json_output:
            import json

            data = self.get_ps_data()
            print(json.dumps(data, indent=2))
        else:
            print("Running containers:\n")
            self.docker.list_containers()

    def wait_for_release(self, timeout: float = 5.0) -> bool:
        """
        Verify that existing containers and socket bindings are fully released
        before initiating container spin-up.
        """
        all_host_ports = []
        for svc in self.config.services.values():
            svc_dict = (
                svc
                if isinstance(svc, dict)
                else (
                    svc.model_dump()
                    if hasattr(svc, "model_dump")
                    else vars(svc)
                )
            )
            all_host_ports.extend(_extract_host_ports(svc_dict))

        if all_host_ports:
            return wait_for_ports_released(list(set(all_host_ports)), timeout=timeout)
        return True

    def restart(self, timeout: float = 5.0):
        """
        Gracefully restart all services managed by DockFleet. This is a convenience wrapper around down() and up().
        Verifies that existing containers and socket bindings are fully released before initiating container spin-up.
        """
        print("Restarting Services...\n")
        self.down()
        self.wait_for_release(timeout=timeout)
        self.up()
        print("\n All services restarted.")

    def get_service_stats(self) -> list[ServiceStat]:
        """Enhanced Docker stats with inspect data."""
        stats: list[ServiceStat] = []

        try:
            result = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--no-trunc",
                    "--format",
                    "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                logger.warning("Docker stats failed")
                return self._get_missing_stats()

            lines = [
                line for line in result.stdout.strip().split("\n")[1:] if line.strip()
            ]

            for line in lines:
                try:
                    parts = line.split("\t")
                    if not parts or not parts[0].strip().startswith("dockfleet_"):
                        continue

                    container = parts[0].strip()
                    service_name = container.replace("dockfleet_", "", 1)

                    cpu_str = parts[1] if len(parts) > 1 else ""
                    mem_usage = parts[2] if len(parts) > 2 else ""
                    mem_perc = parts[3] if len(parts) > 3 else ""

                    try:
                        cleaned_cpu = re.sub(r"[^\d.]", "", cpu_str)
                        cpu = float(cleaned_cpu) if cleaned_cpu else 0.0
                    except (ValueError, TypeError):
                        cpu = 0.0

                    try:
                        if "/" in mem_usage:
                            mem_parts = [p.strip() for p in mem_usage.split("/", 1)]
                            mem_current = mem_parts[0] if mem_parts[0] else "N/A"
                            mem_limit = (
                                mem_parts[1]
                                if len(mem_parts) > 1 and mem_parts[1]
                                else "N/A"
                            )
                        else:
                            mem_current = (
                                mem_usage.strip() if mem_usage.strip() else "N/A"
                            )
                            mem_limit = "N/A"
                        mem_formatted = f"{mem_current}/{mem_limit}"
                    except Exception:
                        mem_formatted = "N/A/N/A"

                    mem_percent_val = mem_perc.strip() if mem_perc else "N/A"
                    uptime = self._get_container_uptime(container)

                    stats.append(
                        ServiceStat(
                            service_name=service_name,
                            container_name=container,
                            cpu_percent=cpu,
                            mem_current=mem_formatted,
                            mem_percent=mem_percent_val,
                            uptime=uptime,
                            status=ContainerStatus.RUNNING,
                        )
                    )
                except Exception as line_err:
                    logger.warning(
                        "Failed to parse container stats for line %r: %s",
                        line,
                        line_err,
                    )
                    try:
                        parts = line.split("\t")
                        if parts and parts[0].strip().startswith("dockfleet_"):
                            c_name = parts[0].strip()
                            s_name = c_name.replace("dockfleet_", "", 1)
                            stats.append(
                                ServiceStat(
                                    service_name=s_name,
                                    container_name=c_name,
                                    cpu_percent=0.0,
                                    mem_current="N/A/N/A",
                                    mem_percent="N/A",
                                    uptime=self._get_container_uptime(c_name),
                                    status=ContainerStatus.RUNNING,
                                )
                            )
                    except Exception:
                        pass

        except Exception as e:
            logger.error("Stats collection failed: %s", e)
            return self._get_missing_stats()

        expected = [f"dockfleet_{name}" for name in self.config.services]
        for container in expected:
            if container not in {s.container_name for s in stats}:
                service_name = container.replace("dockfleet_", "", 1)
                stats.append(
                    ServiceStat(
                        service_name=service_name,
                        container_name=container,
                        status=ContainerStatus.STOPPED,
                    )
                )

        return sorted(stats, key=lambda x: x.service_name)

    def _get_container_uptime(self, container_name: str) -> str:
        """Get uptime from docker inspect."""
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    container_name,
                    "--format",
                    "{{.State.StartedAt}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                started_at = result.stdout.strip()
                if "T" in started_at:
                    time_part = started_at.split("T")[1].split(".")[0].rstrip("Z")
                    return f"Up {time_part}"
                elif started_at:
                    return f"Up {started_at}"
        except Exception:
            pass
        return "Unknown"

    def _get_missing_stats(self) -> list[ServiceStat]:
        """Return all services as unknown."""
        return [
            ServiceStat(
                service_name=name,
                container_name=f"dockfleet_{name}",
                status=ContainerStatus.UNKNOWN,
            )
            for name in self.config.services.keys()
        ]
