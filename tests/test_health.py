from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from sqlmodel import delete, select

from dockfleet.health.logs import (
    DEFAULT_EMPTY_TIMESTAMP,
    _format_created_at,
    iter_logs_as_csv,
    iter_logs_as_text,
    store_log_line,
)
from dockfleet.health.models import LogEvent, Service, get_session, init_db


@pytest.fixture(autouse=True)
def setup_db():
    """Ensure database schema is initialized and clean for each test."""
    init_db()
    with get_session() as session:
        # Create test service if not exists
        svc = session.exec(
            select(Service).where(Service.name == "api_test")
        ).one_or_none()
        if not svc:
            svc = Service(
                name="api_test",
                image="nginx",
                restart_policy="always",
                status="running",
                health_status="healthy",
            )
            session.add(svc)
            session.commit()
    yield
    with get_session() as session:
        session.exec(delete(LogEvent))
        session.commit()


def test_store_log_line_persists_datetime_instance():
    """Assert store_log_line() persists a datetime instance (not str) to LogEvent.created_at."""
    store_log_line(
        "api_test", "test line datetime persistence", level="INFO", source="test"
    )

    with get_session() as session:
        event = session.exec(
            select(LogEvent)
            .where(LogEvent.service_name == "api_test")
            .where(LogEvent.message == "test line datetime persistence")
        ).first()

        assert event is not None
        assert isinstance(event.created_at, datetime)


def test_format_created_at_with_datetime():
    """_format_created_at formats datetime objects to ISO strings."""
    now = datetime.now(timezone.utc)
    result = _format_created_at(now)
    assert result == now.isoformat()


def test_format_created_at_with_legacy_str():
    """_format_created_at handles legacy str input without modification."""
    legacy_str = "2026-09-14T23:29:37.123456"
    result = _format_created_at(legacy_str)
    assert result == legacy_str


def test_format_created_at_with_none():
    """_format_created_at handles None input and returns DEFAULT_EMPTY_TIMESTAMP."""
    result = _format_created_at(None)
    assert result == DEFAULT_EMPTY_TIMESTAMP
    assert result == ""


def test_format_created_at_with_unrecognized_type(caplog):
    """_format_created_at logs a warning for unexpected types and falls back to str()."""
    import logging

    with caplog.at_level(logging.WARNING, logger="dockfleet.health.logs"):
        result = _format_created_at(12345)  # type: ignore

    assert result == "12345"
    assert "unrecognized created_at type" in caplog.text.lower()


def test_store_log_line_and_export_text_end_to_end():
    """Integration test writing a log line and calling iter_logs_as_text end-to-end."""
    store_log_line("api_test", "end-to-end text export log", level="INFO")
    lines = list(iter_logs_as_text("api_test", q="end-to-end text export log"))

    assert len(lines) >= 1
    assert "end-to-end text export log" in lines[0]
    assert "[api_test]" in lines[0]


def test_store_log_line_and_export_csv_end_to_end():
    """Integration test writing a log line and calling iter_logs_as_csv end-to-end."""
    store_log_line("api_test", "end-to-end csv export log", level="INFO")
    chunks = list(iter_logs_as_csv("api_test", q="end-to-end csv export log"))
    full_csv = "".join(chunks)

    assert "service_name,timestamp,level,message,source" in full_csv
    assert "api_test" in full_csv
    assert "end-to-end csv export log" in full_csv


@patch("dockfleet.health.logs.query_logs")
def test_iter_logs_as_text_handles_legacy_string_created_at(mock_query):
    """Regression test: iter_logs_as_text handles legacy string-typed created_at rows without raising AttributeError."""
    mock_event = LogEvent(
        id=1,
        service_id=1,
        service_name="api_test",
        created_at=datetime.now(timezone.utc),
        level="ERROR",
        message="legacy string timestamp message",
        source="legacy-test",
    )
    # Simulate legacy row where created_at was loaded or stored as a string
    mock_event.created_at = "2026-01-01T12:00:00.000000"  # type: ignore
    mock_query.side_effect = [[mock_event], []]

    lines = list(iter_logs_as_text("api_test"))
    assert len(lines) == 1
    assert "[2026-01-01T12:00:00.000000]" in lines[0]
    assert "legacy string timestamp message" in lines[0]


@patch("dockfleet.health.logs.query_logs")
def test_iter_logs_as_csv_handles_legacy_string_created_at(mock_query):
    """Regression test: iter_logs_as_csv handles legacy string-typed created_at rows without raising AttributeError."""
    mock_event = LogEvent(
        id=1,
        service_id=1,
        service_name="api_test",
        created_at=datetime.now(timezone.utc),
        level="WARN",
        message="legacy string csv message",
        source="legacy-csv-test",
    )
    mock_event.created_at = "2026-01-01T12:00:00.000000"  # type: ignore
    mock_query.side_effect = [[mock_event], []]

    chunks = list(iter_logs_as_csv("api_test"))
    full_csv = "".join(chunks)

    assert "2026-01-01T12:00:00.000000" in full_csv
    assert "legacy string csv message" in full_csv
