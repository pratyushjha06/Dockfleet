import logging
import re
import subprocess
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session, select

from dockfleet.cli.config import RestartPolicy
from dockfleet.core.docker import DockerManager
from dockfleet.core.docker_flags import (
    build_env_flags,
    build_port_flags,
    build_resource_flags,
)
from dockfleet.health.models import Service, engine
from dockfleet.health.seed import bootstrap_from_config
from dockfleet.health.status import (
    mark_restart_successful,
    mark_service_running,
    mark_service_stopped,
    record_restart_event,
)
from dockfleet.health.logs import store_log_line

logger = logging.getLogger(__name__)


class ServiceStat(BaseModel):
    service_name: str
    container_name: str
    cpu_percent: Optional[float] = None
    mem_current: Optional[str] = None
    mem_percent: Optional[str] = None
    uptime: Optional[str] = None
    status: str = "unknown"  # running, stopped, missing


_orchestrator_instance = None

def get_container_name(service_name: str) -> str:
    """Shared container name helper for logs."""
    return f"dockfleet_{service_name}"


def get_service_stats(config=None):
    """Module wrapper for stats."""
    orch = get_orchestrator(config)
    return orch.get_service_stats()


def get_orchestrator(config=None, self_healing: bool = True):
    """Get/create global Orchestrator instance."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = Orchestrator(config or {}, self_healing=self_healing)
    return _orchestrator_instance


def restart_service(name: str, config=None) -> bool:
    """Module wrapper for HealthScheduler."""
    orch = get_orchestrator(config)
    return orch.restart_service(name, config)


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

        process.stdout.close()
        process.wait()

    except Exception as e:
        logger.error("Failed to stream logs for %s: %s", container_name, e)
        yield f"Error: {e}"

def normalize_services(services):
    if isinstance(services, list):
        normalized = {}
        for svc in services:
            name = svc.get("name")
            if not name:
                raise ValueError("Service missing 'name'")
            normalized[name] = svc
        return normalized
    return services or {}

class Orchestrator:
    def __init__(self, config, self_healing: bool = True):
        self.config = config
        self.config.services = normalize_services(getattr(config, "services", {}))
        self.self_healing = self_healing
        self.docker = DockerManager()
        self.network = "dockfleet_net"

    def container_name(self, service: str) -> str:
        return f"dockfleet_{service}"

    def start_service(self, name, svc):
        container_name = self.container_name(name)

        try:
            self.docker.remove_container(container_name)

            # Convert to dict safely
            if isinstance(svc, dict):
                service_config = svc
            elif hasattr(svc, "model_dump"):
                service_config = svc.model_dump()
            else:
                service_config = vars(svc)

            #  VALIDATION
            if not service_config.get("image"):
                raise ValueError(f"Service '{name}' missing 'image'")

            #  DEFAULTS (fixes NoneType error)
            service_config["env"] = service_config.get("env") or {}
            service_config["ports"] = service_config.get("ports") or []

            #  FIX env (list → dict)
            if isinstance(service_config["env"], list):
                env_dict = {}
                for item in service_config["env"]:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        env_dict[k] = v
                service_config["env"] = env_dict

            #  FIX ports (dict → list)
            if isinstance(service_config["ports"], dict):
                service_config["ports"] = [
                    f"{k}:{v}" for k, v in service_config["ports"].items()
                ]

            #  Build flags safely
            port_flags = build_port_flags(service_config)
            env_flags = build_env_flags(service_config)
            resource_flags = build_resource_flags(service_config)

            docker_flags = port_flags + env_flags + resource_flags

            #  Always use service_config (not svc)
            self.docker.run_container(
                image=service_config.get("image"),
                name=container_name,
                flags=docker_flags,
                network=self.network,
            )

            mark_service_running(name)
            logger.info("Started service: %s", name)

        except Exception as e:
            logger.error("Failed to start %s: %s", name, e)

    def stop_service(self, name):
        container_name = self.container_name(name)

        try:
            self.docker.stop_container(container_name)
            self.docker.remove_container(container_name)

            mark_service_stopped(name)
            logger.info("Stopped service: %s", name)

        except Exception as e:
            logger.error("Failed to stop %s: %s", name, e)

    def restart_service(
        self,
        service_name: str,
        config=None,
        backoff_attempt: int = 0,
    ) -> bool:
        config = config or self.config

        if not self.self_healing:
            logger.info("Self-healing disabled, skip restart for %s", service_name)
            return False

        if service_name not in config.services:
            logger.warning("Service %s not found", service_name)
            return False

        svc = config.services[service_name]

        if svc.restart == RestartPolicy.never:
            logger.info("%s: restart='never', skipping", service_name)
            return False

        if backoff_attempt > 0:
            import time

            delay = min(2**backoff_attempt, 32)
            logger.info(
                "%s: backoff %ss (attempt %s)",
                service_name,
                delay,
                backoff_attempt,
            )
            time.sleep(delay)

        logger.info("Restarting %s", service_name)

        container_name = self.container_name(service_name)
        try:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    f"name={container_name}",
                    "--format",
                    "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if not result.stdout.strip():
                logger.info("%s not running, skipping", service_name)
                return False
        except Exception:
            logger.warning("Cannot check %s, skipping", service_name)
            return False

        try:
            self.stop_service(service_name)
            self._increment_restart_count(service_name)
            self.start_service(service_name, svc)

            logger.info("%s restarted (count updated)", service_name)
            return True

        except Exception as e:
            logger.error("%s restart FAILED: %s", service_name, e)
            return False

    def _increment_restart_count(self, service_name: str) -> None:
        try:
            with Session(engine) as session:
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

    def handle_unhealthy_service(
        self,
        service_name: str,
        config=None,
        reason: str = "health failure",
    ) -> None:
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
            logger.error("restart_service failed %s", service_name)
            self._mark_restart_failed(
                service_name,
                "restart_service returned False",
            )
            return

        logger.info("%s auto-restarted", service_name)
        mark_restart_successful(service_name)

        with Session(engine) as session:
            svc = session.exec(
                select(Service).where(Service.name == service_name)
            ).one_or_none()
            if svc:
                record_restart_event(svc, reason)
            else:
                logger.warning("Service %s not found after restart", service_name)

    def _mark_restart_failed(self, service_name: str, reason: str) -> None:
        with Session(engine) as session:
            svc = session.exec(
                select(Service).where(Service.name == service_name)
            ).one_or_none()
            if svc:
                svc.status = "crashed"
                svc.last_failure_reason = f"auto-restart failed: {reason}"
                session.add(svc)
                session.commit()
                logger.error("%s marked CRASHED: %s", service_name, reason)

    def _resolve_service_order(self):
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
        print("Starting services...\n")

        bootstrap_from_config(self.config)
        print(" DB bootstrapped & services seeded")

        self.docker.create_network(self.network)

        order = self._resolve_service_order()

        for name in order:
            svc = self.config.services[name]
            self.start_service(name, svc)

    def down(self):
        print("Stopping services...\n")

        for name in self.config.services.keys():
            self.stop_service(name)


    def ps(self):
        print("Running containers:\n")
        self.docker.list_containers()

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
                line
                for line in result.stdout.strip().split("\n")[1:]
                if line.strip()
            ]

            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 4 and parts[0].startswith("dockfleet_"):
                    container = parts[0].strip()
                    service_name = container.replace("dockfleet_", "")

                    cpu_str, mem_usage, mem_perc = parts[1:4]
                    cpu = (
                        float(re.sub(r"[^\d.]", "", cpu_str))
                        if cpu_str != "0.00%"
                        else 0.0
                    )
                    mem_current, mem_limit = mem_usage.split("/")
                    uptime = self._get_container_uptime(container)

                    stats.append(
                        ServiceStat(
                            service_name=service_name,
                            container_name=container,
                            cpu_percent=cpu,
                            mem_current=f"{mem_current}/{mem_limit}",
                            mem_percent=mem_perc.strip(),
                            uptime=uptime,
                            status="running",
                        )
                    )

        except Exception as e:
            logger.error("Stats collection failed: %s", e)
            return self._get_missing_stats()

        expected = [f"dockfleet_{name}" for name in self.config.services]
        for container in expected:
            if container not in {s.container_name for s in stats}:
                service_name = container.replace("dockfleet_", "")
                stats.append(
                    ServiceStat(
                        service_name=service_name,
                        container_name=container,
                        status="stopped",
                    )
                )

        return sorted(stats, key=lambda x: x.service_name)

    def _get_container_uptime(self, container_name: str) -> str:
        """Get uptime from docker inspect."""
        try:
            result = subprocess.run(
                ["docker", "inspect", container_name, "--format", "{{.State.StartedAt}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                started_at = result.stdout.strip()
                return f"Up {started_at.split('T')[1].split('.')[0]}"
        except Exception:
            pass
        return "Unknown"

    def _get_missing_stats(self) -> list[ServiceStat]:
        """Return all services as unknown."""
        return [
            ServiceStat(
                service_name=name,
                container_name=f"dockfleet_{name}",
                status="unknown",
            )
            for name in self.config.services.keys()
        ]