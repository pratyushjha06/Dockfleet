from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlmodel import func, select

from .models import LogEvent, Service, get_engine, get_session

logger = logging.getLogger(__name__)

DEFAULT_EMPTY_TIMESTAMP: str = ""


def _csv_field(value: str) -> str:
    """
    Escape a single CSV field per RFC 4180.

    If the value contains a double-quote, comma, or newline the function:
    1. Replaces every ``"`` with ``""`` (RFC 4180 §2.7).
    2. Wraps the resulting string in double-quotes.

    Fields that contain none of those characters are returned as-is.
    """
    if '"' in value or "," in value or "\n" in value:
        return '"' + value.replace('"', '""') + '"'
    return value


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


_SERVICE_ID_CACHE: dict[str, dict[str, int]] = {}

def store_log_line(
    service_name: str,
    message: str,
    level: str | None = None,
    source: str | None = None,
) -> None:
    """
    Store a single log metadata row for later search/analytics.

    - Looks up Service by name and attaches service_id + service_name.
    - Uses an in-memory cache to avoid O(N) SELECT queries during log ingestion.
    - Skips insert (with a warning) if the service is not present in the DB.
    - Persists created_at as a timezone-aware datetime instance (UTC).
    """
    db_url = str(get_engine().url)
    cache = _SERVICE_ID_CACHE.setdefault(db_url, {})

    if service_name not in cache:
        with get_session() as session:
            svc = session.exec(
                select(Service).where(Service.name == service_name)
            ).one_or_none()
            if svc is not None:
                assert svc.id is not None
                cache[service_name] = svc.id

    service_id = cache.get(service_name)
    if service_id is None:
        print(f"[logs] Service '{service_name}' not found in DB, skipping log")
        return

    with get_session() as session:
        event = LogEvent(
            service_id=service_id,
            service_name=service_name,
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
    cursor_ts: datetime | str | None = None,
    cursor_id: int | None = None,
) -> list[LogEvent]:
    """
    High-level helper to fetch LogEvent rows with optional filters
    and pagination.

    - service_name: match on LogEvent.service_name (case-insensitive)
    - q: substring search on message (case-sensitive for now)
    - limit/offset: standard pagination (O(N), less efficient)
    - cursor_ts/cursor_id: keyset pagination for fast sequential scanning (O(1))
    """
    # Coerce parameters in case called directly as python function with FastAPI default objects
    if not isinstance(limit, int):
        limit = 50
    if not isinstance(offset, int):
        offset = 0

    # hard cap for safety
    limit = min(limit, 1000)

    with get_session() as session:
        stmt = select(LogEvent)

        if service_name:
            stmt = stmt.where(func.lower(LogEvent.service_name) == service_name.lower())

        if q:
            pattern = f"%{q}%"
            # SQLite: LIKE (case-sensitive by default); can be tuned later.
            stmt = stmt.where(LogEvent.message.like(pattern))  # type: ignore

        if cursor_ts is not None and cursor_id is not None:
            # Deterministic keyset pagination for descending order
            stmt = stmt.where(
                (LogEvent.created_at < cursor_ts) |  # type: ignore
                ((LogEvent.created_at == cursor_ts) & (LogEvent.id < cursor_id))  # type: ignore
            )

        stmt = stmt.order_by(LogEvent.created_at.desc(), LogEvent.id.desc())  # type: ignore
        
        if offset > 0:
            stmt = stmt.offset(offset)
            
        stmt = stmt.limit(limit)
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
    cursor_ts = None
    cursor_id = None

    while True:
        batch = query_logs(
            service_name=service_name,
            q=q,
            limit=batch_size,
            cursor_ts=cursor_ts,
            cursor_id=cursor_id,
        )
        if not batch:
            break

        for event in batch:
            ts = _format_created_at(event.created_at)
            service = event.service_name or ""
            msg = event.message or ""
            yield f"[{ts}] [{service}] {msg}\n"

        last_event = batch[-1]
        cursor_ts = last_event.created_at
        cursor_id = last_event.id


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

    cursor_ts = None
    cursor_id = None

    while True:
        batch = query_logs(
            service_name=service_name,
            q=q,
            limit=batch_size,
            cursor_ts=cursor_ts,
            cursor_id=cursor_id,
        )
        if not batch:
            break

        lines: list[str] = []
        for event in batch:
            service = event.service_name or ""
            ts = _format_created_at(event.created_at)
            level = event.level or ""
            # Newlines within the message body are represented as literal \n so
            # that each log event stays on a single CSV row.  RFC 4180 quoting
            # and inner-quote escaping are handled uniformly by _csv_field.
            msg = (event.message or "").replace("\n", "\\n")
            source = event.source or ""

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

        last_event = batch[-1]
        cursor_ts = last_event.created_at
        cursor_id = last_event.id
