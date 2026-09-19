import json
import subprocess

from sqlmodel import select

from dockfleet.health.models import ContainerStatus, HealthStatus
from dockfleet.health.models import Service as DBService
from dockfleet.health.models import get_session


def get_services() -> list[dict]:
    """
    Fetch all services from database merged with real-time Docker runtime container and resource stats.
    """
    services = {}

    # -------------------
    # 1. Load DB services
    # -------------------
    with get_session() as session:
        db_services = session.exec(select(DBService)).all()

        for svc in db_services:
            health_st = getattr(svc, "health_status", HealthStatus.HEALTHY)
            if isinstance(health_st, HealthStatus):
                health_st = health_st.value

            services[svc.name] = {
                "name": svc.name,
                "status": ContainerStatus.STOPPED.value,  # default → will override
                "health_status": health_st,
                "image": svc.image,
                "ports": svc.ports_raw,
                "restart_policy": svc.restart_policy,
                "restart_count": svc.restart_count,
                "last_health_check": getattr(svc, "last_health_check", None),
                # runtime
                "cpu": "0%",
                "memory": "0MB",
                "uptime": "stopped",
                "cpu_limit": None,
                "memory_limit": None,
            }

    # -------------------
    # 2. Get ALL containers (running + stopped)
    # -------------------
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
        )

        for line in result.stdout.splitlines():
            if not line:
                continue

            container = json.loads(line)
            name = container.get("Names") or container.get("Name") or ""
            if isinstance(name, list):
                name = name[0] if name else ""
            if not isinstance(name, str) or not name.startswith("dockfleet_"):
                continue

            service_name = name.replace("dockfleet_", "")

            if service_name not in services:
                continue

            status_raw = container.get("Status", "")

            # -------------------
            # Normalize status
            # -------------------
            if "Up" in status_raw:
                status = ContainerStatus.RUNNING.value
            elif "Restarting" in status_raw:
                status = HealthStatus.RESTARTING.value
            elif "Exited" in status_raw:
                status = ContainerStatus.STOPPED.value
            else:
                status = ContainerStatus.UNKNOWN.value

            services[service_name]["status"] = status
            services[service_name]["uptime"] = container.get("RunningFor")

            # sync health_status with real state, but preserve failing or restarting states
            if status == ContainerStatus.RUNNING.value:
                # Preserve failing or restarting states from health checks
                if services[service_name]["health_status"] not in (
                    HealthStatus.UNHEALTHY.value,
                    HealthStatus.CRASHED.value,
                    HealthStatus.RESTARTING.value,
                ):
                    services[service_name]["health_status"] = HealthStatus.HEALTHY.value
            elif status == HealthStatus.RESTARTING.value:
                services[service_name]["health_status"] = HealthStatus.RESTARTING.value
            elif status == ContainerStatus.STOPPED.value:
                # only downgrade to "stopped" if we don't already know it's crashed
                if services[service_name]["health_status"] not in (
                    HealthStatus.CRASHED.value,
                    HealthStatus.CRASHED,
                ):
                    services[service_name][
                        "health_status"
                    ] = ContainerStatus.STOPPED.value

    except Exception as e:
        print("Docker ps -a failed:", e)

    # -------------------
    # 3. Fetch CPU + memory stats (only running)
    # -------------------
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
        )

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            container = json.loads(line)
            name = container.get("Names") or container.get("Name") or ""
            if isinstance(name, list):
                name = name[0] if name else ""
            if not isinstance(name, str) or not name.startswith("dockfleet_"):
                continue

            service_name = name.replace("dockfleet_", "")

            if service_name in services:
                services[service_name]["cpu"] = container.get("CPUPerc")
                services[service_name]["memory"] = container.get("MemUsage")

    except Exception as e:
        print("Docker stats failed:", e)

    return list(services.values())
