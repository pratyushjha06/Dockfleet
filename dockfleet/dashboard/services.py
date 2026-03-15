import subprocess
import json
from datetime import datetime
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
                "health_status": svc.status or "unknown",
                "image": svc.image,
                "ports": svc.ports_raw,
                "restart_policy": svc.restart_policy,
                "restart_count": svc.restart_count,
                "last_health_check": svc.last_health_check,

                # new runtime fields
                "cpu": "0%",
                "memory": "0MB",
                "uptime": "unknown",
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
    