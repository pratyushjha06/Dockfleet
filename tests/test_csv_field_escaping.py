"""
Tests for iter_logs_as_csv RFC 4180 compliant escaping (issue #315).

Verifies that:
1. Fields containing double-quotes are correctly escaped per RFC 4180
   (inner " doubled to "", entire field wrapped in "...").
2. Fields containing commas are quoted.
3. Fields containing newlines are quoted.
4. Plain fields (no special chars) are passed through as-is.
5. _csv_field is a module-level function (not re-created per iteration).
6. iter_logs_as_csv output is parseable by Python csv.reader without errors
   and round-trips the original values correctly.
"""

import csv
import io
from datetime import datetime, timezone

import pytest

from dockfleet.health.logs import _csv_field, iter_logs_as_csv
from dockfleet.health.models import LogEvent, Service, get_session, init_db
from sqlmodel import select


# ---------------------------------------------------------------------------
# Unit tests for the _csv_field helper
# ---------------------------------------------------------------------------


class TestCsvFieldHelper:

    def test_plain_value_unchanged(self):
        assert _csv_field("hello") == "hello"
        assert _csv_field("running") == "running"
        assert _csv_field("") == ""

    def test_comma_triggers_quoting(self):
        assert _csv_field("a,b") == '"a,b"'

    def test_double_quote_escaped_and_quoted(self):
        result = _csv_field('say "hello"')
        assert result == '"say ""hello"""'

    def test_newline_triggers_quoting(self):
        result = _csv_field("line1\nline2")
        assert result == '"line1\nline2"'

    def test_multiple_quotes_all_doubled(self):
        result = _csv_field('"a","b"')
        assert result == '"""a"",""b"""'

    def test_only_quote_still_quoted(self):
        assert _csv_field('"') == '""""'

    def test_is_module_level_not_closure(self):
        # Module-level functions have no __closure__
        assert _csv_field.__closure__ is None, (
            "_csv_field should be module-level with no closure"
        )


# ---------------------------------------------------------------------------
# Fixtures / helpers for integration tests
# ---------------------------------------------------------------------------

_DB_URL = "sqlite://"


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    monkeypatch.setenv("DOCKFLEET_DB_URL", _DB_URL)
    from dockfleet.health import models as _models
    _models.reset_engine_cache()
    init_db(db_url=_DB_URL)
    yield
    _models.reset_engine_cache()


def _ensure_svc(name: str) -> int:
    with get_session(db_url=_DB_URL) as session:
        svc = session.exec(select(Service).where(Service.name == name)).one_or_none()
        if svc is None:
            svc = Service(name=name, image="img", restart_policy="always")
            session.add(svc)
            session.commit()
            session.refresh(svc)
        return svc.id


def _seed(name: str, message: str, level: str = "INFO", source: str = "test") -> None:
    svc_id = _ensure_svc(name)
    with get_session(db_url=_DB_URL) as session:
        event = LogEvent(
            service_id=svc_id,
            service_name=name,
            created_at=datetime.now(timezone.utc),
            level=level,
            message=message,
            source=source,
        )
        session.add(event)
        session.commit()


def _parse_csv(chunks) -> list[list[str]]:
    body = "".join(chunks)
    reader = csv.reader(io.StringIO(body))
    return list(reader)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIterLogsAsCsvEscaping:

    def test_csv_header_present(self):
        rows = _parse_csv(iter_logs_as_csv())
        assert rows[0] == ["service_name", "timestamp", "level", "message", "source"]

    def test_plain_message_round_trips(self):
        _seed("api", "service started")
        rows = _parse_csv(iter_logs_as_csv())
        data = rows[1:]
        assert any(r[3] == "service started" for r in data)

    def test_message_with_double_quote_round_trips(self):
        _seed("api", 'error: "connection refused"')
        rows = _parse_csv(iter_logs_as_csv())
        data = rows[1:]
        assert any(r[3] == 'error: "connection refused"' for r in data), (
            f"Quote in message not round-tripped. rows={data}"
        )

    def test_level_with_double_quote_round_trips(self):
        _seed("api", "normal msg", level='WARN"injected')
        rows = _parse_csv(iter_logs_as_csv())
        data = rows[1:]
        assert any(r[2] == 'WARN"injected' for r in data), (
            f"Quote in level not round-tripped. rows={data}"
        )

    def test_source_with_comma_round_trips(self):
        _seed("api", "msg", source="docker,logs")
        rows = _parse_csv(iter_logs_as_csv())
        data = rows[1:]
        assert any(r[4] == "docker,logs" for r in data), (
            f"Comma in source not round-tripped. rows={data}"
        )

    def test_row_count_is_correct(self):
        _seed("api", 'msg with "quotes" and, commas')
        _seed("api", "plain msg")
        rows = _parse_csv(iter_logs_as_csv())
        assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"

    def test_embedded_newline_becomes_backslash_n(self):
        _seed("api", "line1\nline2")
        rows = _parse_csv(iter_logs_as_csv())
        data = rows[1:]
        assert any(r[3] == "line1\\nline2" for r in data), (
            f"Embedded newline not escaped to \\n. rows={data}"
        )

    def test_csv_parseable_without_error(self):
        _seed("svc,name", 'msg "quoted"', level='ERR"OR', source="src,1")
        try:
            rows = _parse_csv(iter_logs_as_csv())
        except csv.Error as exc:
            pytest.fail(f"iter_logs_as_csv produced unparseable CSV: {exc}")
        assert len(rows) >= 2
