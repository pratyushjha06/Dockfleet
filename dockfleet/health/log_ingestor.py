# dockfleet/health/log_ingestor.py

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .models import Service, LogEvent, engine


def ingest_docker_logs_once(tail: int = 200) -> None:
    """
    Pull last `tail` docker logs for every known Service and store them
    into LogEvent for /logs/db and /logs/download.

    Idempotency-ish guard: we ensure created_at is strictly increasing
    per service so repeated runs don't break ordering.
    """
    with Session(engine) as session:
        services = session.exec(select(Service)).all()

        for svc in services:
            name = svc.name
            container = f"dockfleet_{name}"

            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                # container may not exist or be stopped; skip
                continue

            # latest log timestamp we already have for this service
            latest_ts: datetime | None = session.exec(
                select(LogEvent.created_at)
                .where(LogEvent.service_name == name)
                .order_by(LogEvent.created_at.desc())
                .limit(1)
            ).one_or_none()

            for line in result.stdout.splitlines():
                line = line.rstrip()
                if not line:
                    continue

                now = datetime.utcnow()
                if latest_ts is not None and now <= latest_ts:
                    now = latest_ts + timedelta(microseconds=1)

                event = LogEvent(
                    service_id=svc.id,
                    service_name=name,
                    created_at=now,
                    level=None,
                    message=line,
                    source="docker-logs-ingestor",
                )
                session.add(event)
                latest_ts = now

        session.commit()
