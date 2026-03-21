import subprocess
import json
from sqlmodel import Session, select
from dockfleet.health.models import Service as DBService, engine


def get_services():
    services = {}

    # -------------------
    # 1. Load DB services
    # -------------------
    with Session(engine) as session:
        db_services = session.exec(select(DBService)).all()

        for svc in db_services:
            services[svc.name] = {
                "name": svc.name,
                "status": "stopped",  # default → will override
                "health_status": getattr(svc, "health_status", "unknown"),
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
            text=True
        )

        for line in result.stdout.splitlines():
            if not line:
                continue

            container = json.loads(line)
            name = container.get("Names")

            if not name.startswith("dockfleet_"):
                continue

            service_name = name.replace("dockfleet_", "")

            if service_name not in services:
                continue

            status_raw = container.get("Status", "")

            # -------------------
            # 🔥 Normalize status
            # -------------------
            if "Up" in status_raw:
                status = "running"
            elif "Restarting" in status_raw:
                status = "restarting"
            elif "Exited" in status_raw:
                status = "stopped"
            else:
                status = "unknown"

            services[service_name]["status"] = status
            services[service_name]["uptime"] = container.get("RunningFor")

            # 🔥 sync health_status with real state
            if status == "running":
                services[service_name]["health_status"] = "healthy"
            elif status == "restarting":
                services[service_name]["health_status"] = "restarting"
            elif status == "stopped":
                services[service_name]["health_status"] = "stopped"

    except Exception as e:
        print("Docker ps -a failed:", e)

    # -------------------
    # 3. Fetch CPU + memory stats (only running)
    # -------------------
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True
        )

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            container = json.loads(line)
            name = container.get("Name")

            if not name.startswith("dockfleet_"):
                continue

            service_name = name.replace("dockfleet_", "")

            if service_name in services:
                services[service_name]["cpu"] = container.get("CPUPerc")
                services[service_name]["memory"] = container.get("MemUsage")

    except Exception as e:
        print("Docker stats failed:", e)

    return list(services.values())
    