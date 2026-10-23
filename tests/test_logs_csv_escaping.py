"""Regression tests for RFC 4180 escaping in the CSV log export.

The exporter used to wrap fields without doubling embedded quotes, so a
value like ``WARN"injected`` produced a corrupt row.  These tests pin the
escaping for every column and verify the output parses back to the original
values.
"""

import csv
import io
from datetime import datetime, timezone
from unittest.mock import patch

from dockfleet.health.logs import iter_logs_as_csv
from dockfleet.health.models import LogEvent

CREATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
TIMESTAMP = "2026-01-01T12:00:00+00:00"


def _event(**overrides) -> LogEvent:
    values = {
        "id": 1,
        "service_id": 1,
        "service_name": "api",
        "created_at": CREATED_AT,
        "level": "INFO",
        "message": "started",
        "source": "orchestrator",
    }
    values.update(overrides)
    return LogEvent(**values)


def _parse_csv(events: list[LogEvent]) -> list[list[str]]:
    with patch("dockfleet.health.logs.query_logs", side_effect=[events, []]):
        body = "".join(iter_logs_as_csv())

    return list(csv.reader(io.StringIO(body)))


def test_plain_rows_are_not_quoted():
    rows = _parse_csv([_event()])

    assert rows[0] == ["service_name", "timestamp", "level", "message", "source"]
    assert rows[1] == ["api", TIMESTAMP, "INFO", "started", "orchestrator"]


def test_quotes_in_level_and_source_are_doubled():
    rows = _parse_csv([_event(level='WARN"injected', source='multi"source')])

    assert rows[1] == ["api", TIMESTAMP, 'WARN"injected', "started", 'multi"source']


def test_comma_and_quote_in_service_name_are_escaped():
    rows = _parse_csv([_event(service_name='api,"primary"')])

    assert rows[1] == ['api,"primary"', TIMESTAMP, "INFO", "started", "orchestrator"]


def test_embedded_newline_in_message_survives_round_trip():
    rows = _parse_csv([_event(message="line one\nline two")])

    assert rows[1] == ["api", TIMESTAMP, "INFO", "line one\nline two", "orchestrator"]


def test_embedded_quote_in_message_survives_round_trip():
    rows = _parse_csv([_event(message='container said "ready"')])

    assert rows[1] == [
        "api",
        TIMESTAMP,
        "INFO",
        'container said "ready"',
        "orchestrator",
    ]
