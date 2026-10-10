from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import datetime, timezone

from sqlmodel import select

from .models import LogCursor, LogEvent, Service, get_session

_DOCKER_TS_REGEX = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


def _parse_docker_timestamp(ts_str: str) -> datetime | None:
    """Parse RFC3339 / ISO 8601 Docker log timestamp into UTC datetime."""
    if not ts_str:
        return None
    try:
        match = _DOCKER_TS_REGEX.match(ts_str)
        if match:
            base, frac, tz = match.groups()
            base = base.replace(" ", "T")
            if frac:
                frac = frac[:6].ljust(6, "0")
                base = f"{base}.{frac}"
            if tz:
                if tz == "Z":
                    tz = "+00:00"
                elif len(tz) == 5 and (tz[0] in "+-") and ":" not in tz:
                    tz = f"{tz[:3]}:{tz[3:]}"
                base = f"{base}{tz}"
            else:
                base = f"{base}+00:00"
            dt = datetime.fromisoformat(base)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    return None


def _normalize_utc_datetime(dt: datetime | str | None) -> datetime | None:
    """Normalize any datetime (naive or timezone-aware) or timestamp string to UTC timezone-aware datetime."""
    if dt is None:
        return None
    if isinstance(dt, str):
        parsed = _parse_docker_timestamp(dt)
        if parsed is not None:
            return parsed
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


import logging

logger = logging.getLogger(__name__)


def _ingest_single_service_logs(svc_id: int | None, name: str, tail: int) -> None:
    container = f"dockfleet_{name}"

    with get_session() as session:
        # Fetch docker source timestamp cursor
        cursor_row = session.exec(
            select(LogCursor).where(LogCursor.service_id == svc_id)
        ).one_or_none()
        cursor_ts_str = cursor_row.last_timestamp if cursor_row else None

        cmd = ["docker", "logs", "--timestamps"]
        if cursor_ts_str is not None:
            cmd.extend(["--since", cursor_ts_str])
        else:
            cmd.extend(["--tail", str(tail)])
        cmd.append(container)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.error("Error streaming docker logs for %s: %s", name, e)
            print(f"Error streaming docker logs for {name}: {e}")
            return

        with tempfile.TemporaryFile(
            mode="w+t", encoding="utf-8", errors="replace"
        ) as spool:
            # Stage to disk (tempfile) to avoid memory blowup while we wait for success
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    spool.write(line + "\n")

            process.stdout.close()
            if process.wait() != 0:
                logger.warning(
                    "Docker logs process exited with non-zero status for %s", name
                )
                session.rollback()
                return

            spool.seek(0)
            batch_count = 0
            last_source_ts = cursor_ts_str

            boundary_records: list[tuple[datetime, str]] = []
            cursor_dt = (
                _normalize_utc_datetime(_parse_docker_timestamp(cursor_ts_str))
                if cursor_ts_str
                else None
            )
            if cursor_dt is not None:
                existing_events = session.exec(
                    select(LogEvent)
                    .where(LogEvent.service_id == svc_id)
                    .order_by(LogEvent.id.desc())
                    .limit(500)
                ).all()
                for e in existing_events:
                    e_dt = _normalize_utc_datetime(e.created_at)
                    if e_dt is not None and e_dt >= cursor_dt:
                        boundary_records.append((e_dt, e.message or ""))

            for line in spool:
                line = line.rstrip()
                if not line:
                    continue

                message = line
                log_created_at = None
                raw_ts = None
                if " " in line:
                    potential_ts, msg = line.split(" ", 1)
                    parsed_dt = _parse_docker_timestamp(potential_ts)
                    if parsed_dt is not None:
                        raw_ts = potential_ts
                        message = msg
                        log_created_at = parsed_dt

                if log_created_at is None:
                    log_created_at = datetime.now(timezone.utc)
                else:
                    log_created_at = _normalize_utc_datetime(log_created_at)

                # Boundary deduplication for incremental polling
                if cursor_dt is not None and raw_ts is not None and log_created_at is not None:
                    if log_created_at < cursor_dt:
                        continue
                    if log_created_at == cursor_dt:
                        match_item = (log_created_at, message)
                        if match_item in boundary_records:
                            boundary_records.remove(match_item)
                            continue

                if raw_ts:
                    last_source_ts = raw_ts

                event = LogEvent(
                    service_id=svc_id,
                    service_name=name,
                    created_at=log_created_at,
                    level=None,
                    message=message,
                    source="docker-logs-ingestor",
                )
                session.add(event)

                batch_count += 1
                if batch_count >= 1000:
                    if last_source_ts:
                        if not cursor_row:
                            cursor_row = LogCursor(
                                service_id=svc_id,
                                last_timestamp=last_source_ts,
                            )
                            session.add(cursor_row)
                        else:
                            cursor_row.last_timestamp = last_source_ts
                    session.commit()
                    batch_count = 0

            if batch_count > 0:
                if last_source_ts:
                    if not cursor_row:
                        cursor_row = LogCursor(
                            service_id=svc_id, last_timestamp=last_source_ts
                        )
                        session.add(cursor_row)
                    else:
                        cursor_row.last_timestamp = last_source_ts
                session.commit()


def ingest_docker_logs_once(tail: int = 200) -> None:
    """
    Pull last `tail` docker logs for every known Service and store them
    into LogEvent for /logs/db and /logs/download.
    """
    with get_session() as session:
        services = session.exec(select(Service)).all()
        service_info = [(svc.id, svc.name) for svc in services]

    for svc_id, name in service_info:
        try:
            _ingest_single_service_logs(svc_id, name, tail)
        except Exception as e:
            logger.error("Error ingesting logs for service %s: %s", name, e)
            print(f"Error ingesting logs for service {name}: {e}")
