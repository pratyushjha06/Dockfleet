import subprocess
import json
from sqlmodel import Session, select
from dockfleet.health.models import Service as DBService, engine


def get_services():

    services = {}

    # -------------------
    # Load services from DB
    # -------------------

    with Session(engine) as session:

        db_services = session.exec(select(DBService)).all()

        for svc in db_services:

            services[svc.name] = {
                "name": svc.name,
                "status": svc.status or "stopped",
                "health_status": getattr(svc, "health_status", "unknown"),
                "image": svc.image,
                "ports": svc.ports_raw,
                "restart_policy": svc.restart_policy,
                "restart_count": svc.restart_count,
                "last_health_check": getattr(svc, "last_health_check", None),

                # new runtime fields
                "cpu": "0%",
                "memory": "0MB",
                "uptime": "unknown",
                "cpu_limit": None,
                "memory_limit": None,
            }

    # -------------------
    # Fetch runtime stats
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

            if name.startswith("dockfleet_"):
                name = name.replace("dockfleet_", "")

            if name in services:

                services[name]["cpu"] = container.get("CPUPerc")
                services[name]["memory"] = container.get("MemUsage")

    except Exception as e:
        print("Docker stats failed:", e)

    # -------------------
    # Fetch container status
    # -------------------

    try:

        result = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True,
            text=True
        )

        for line in result.stdout.splitlines():

            container = json.loads(line)

            name = container.get("Names")

            if name.startswith("dockfleet_"):
                name = name.replace("dockfleet_", "")

            if name in services:

                services[name]["status"] = "running"
                services[name]["uptime"] = container.get("RunningFor")

    except Exception as e:
        print("Docker ps failed:", e)

    return list(services.values())

def get_logs(service: str = None, limit: int = 50):

    try:
        cmd = ["docker", "logs", "--tail", str(limit)]

        if service and service != "all":
            cmd.append(f"dockfleet_{service}")
        else:
            # fallback: show logs of all running containers
            cmd = ["docker", "ps", "--format", "{{.Names}}"]

            result = subprocess.run(cmd, capture_output=True, text=True)
            containers = result.stdout.strip().split("\n")

            logs = []

            for c in containers:
                log_cmd = ["docker", "logs", "--tail", "20", c]
                res = subprocess.run(log_cmd, capture_output=True, text=True)

                for line in res.stdout.splitlines():
                    logs.append({
                        "service": c.replace("dockfleet_", ""),
                        "message": line
                    })

            return logs[::-1]  # latest first

        # single service logs
        result = subprocess.run(cmd, capture_output=True, text=True)

        logs = []
        for line in result.stdout.splitlines():
            logs.append({
                "service": service,
                "message": line
            })

        return logs[::-1]

    except Exception as e:
        print("Logs error:", e)
        return []
