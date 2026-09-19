from collections.abc import Iterable
from datetime import datetime, timezone
import logging

from sqlmodel import func, select

from .models import LogEvent, Service, get_session

logger = logging.getLogger(__name__)

DEFAULT_EMPTY_TIMESTAMP: str = ""


def _format_created_at(value: datetime | str | None) -> str:
    """
    Format a LogEvent created_at timestamp value to an ISO string representation.

    Accepts:
    - datetime: Formatted via value.isoformat()
    - str: Returned as-is (backward compatibility: supports legacy rows written prior to this fix)
    - None: Returns DEFAULT_EMPTY_TIMESTAMP ("")
    - Other types: Logs a warning and falls back to str(value)
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        # Backward compatibility: handle legacy string-typed created_at rows written before the fix
        return value
    if value is None:
        return DEFAULT_EMPTY_TIMESTAMP

    logger.warning("Unrecognized created_at type %s for value: %r", type(value), value)
    return str(value)


def store_log_line(
    service_name: str,
    message: str,
    level: str | None = None,
    source: str | None = None,
) -> None:
    """
    Store a single log metadata row for later search/analytics.

    - Looks up Service by name and attaches service_id + service_name.
    - Skips insert (with a warning) if the service is not present in the DB.
    - Persists created_at as a timezone-aware datetime instance (UTC).
    """
    with get_session() as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()

        if svc is None:
            print(f"[logs] Service '{service_name}' not found in DB, skipping log")
            return

        event = LogEvent(
            service_id=svc.id,
            service_name=svc.name,
            created_at=datetime.now(timezone.utc),
            level=level,
            message=message,
            source=source,
        )

        session.add(event)
        session.commit()


def query_logs(
    service_name: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LogEvent]:
    """
    High-level helper to fetch LogEvent rows with optional filters
    and pagination.

    - service_name: match on LogEvent.service_name (case-insensitive)
    - q: substring search on message (case-sensitive for now)
    - limit/offset: pagination for dashboard /logs/db and /logs/download
    """
    # hard cap for safety
    limit = min(limit, 1000)

    with get_session() as session:
        stmt = select(LogEvent)

        if service_name:
            stmt = stmt.where(func.lower(LogEvent.service_name) == service_name.lower())

        if q:
            pattern = f"%{q}%"
            # SQLite: LIKE (case-sensitive by default); can be tuned later.
            stmt = stmt.where(LogEvent.message.like(pattern))

        stmt = stmt.order_by(LogEvent.created_at.desc()).offset(offset).limit(limit)
        events = session.exec(stmt).all()

    return list(events)


def iter_logs_as_text(
    service_name: str | None = None,
    q: str | None = None,
    batch_size: int = 1000,
) -> Iterable[str]:
    """
    Generator that yields logs as plain text lines, suitable for
    StreamingResponse in the /logs/download?format=text endpoint.

    Format per line:
        [timestamp] [service_name] message
    """
    offset = 0

    while True:
        batch = query_logs(
            service_name=service_name,
            q=q,
            limit=batch_size,
            offset=offset,
        )
        if not batch:
            break

        for event in batch:
            ts = _format_created_at(event.created_at)
            service = event.service_name or ""
            msg = event.message or ""
            yield f"[{ts}] [{service}] {msg}\n"

        offset += batch_size


def iter_logs_as_csv(
    service_name: str | None = None,
    q: str | None = None,
    batch_size: int = 1000,
) -> Iterable[str]:
    """
    Generator that yields CSV chunks (as strings) for log download.

    Columns:
        service_name,timestamp,level,message,source
    """
    # header
    yield "service_name,timestamp,level,message,source\n"

    offset = 0

    while True:
        batch = query_logs(
            service_name=service_name,
            q=q,
            limit=batch_size,
            offset=offset,
        )
        if not batch:
            break

        lines: list[str] = []
        for event in batch:
            service = event.service_name or ""
            ts = _format_created_at(event.created_at)
            level = event.level or ""
            msg = (event.message or "").replace("\n", "\\n").replace('"', '""')
            source = event.source or ""

            # minimal CSV-escaping: wrap fields containing commas/quotes/newlines
            def _csv_field(value: str) -> str:
                if "," in value or '"' in value or "\n" in value:
                    return f'"{value}"'
                return value

            line = ",".join(
                [
                    _csv_field(service),
                    _csv_field(ts),
                    _csv_field(level),
                    _csv_field(msg),
                    _csv_field(source),
                ]
            )
            lines.append(line)

        if lines:
            yield "\n".join(lines) + "\n"

        offset += batch_size
