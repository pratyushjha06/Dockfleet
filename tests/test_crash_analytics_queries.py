

import pytest
from datetime import datetime, timedelta
from sqlmodel import Session, SQLModel, create_engine
from unittest.mock import patch

from dockfleet.health.models import Service, RestartEvent
from dockfleet.health.queries import (
    get_failure_reasons_breakdown,
    get_most_unstable_services,
    get_restart_history,
)


# ------------------------------------------------
# In-memory SQLite engine for tests (no file left behind)
# ------------------------------------------------
TEST_DB_URL = "sqlite://"  # pure in-memory


@pytest.fixture(name="engine")
def engine_fixture():
    """Create a fresh in-memory DB for every test."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="session")
def session_fixture(engine):
    """Provide a session backed by the in-memory engine."""
    with Session(engine) as session:
        yield session


@pytest.fixture(name="seeded_service")
def seeded_service_fixture(session, engine):
    """
    Insert one Service row and several RestartEvents with
    different reasons so analytics queries have real data.
    """
    svc = Service(
        name="api",
        image="nginx:latest",
        restart_policy="always",
        status="unhealthy",
        restart_count=7,
    )
    session.add(svc)
    session.commit()
    session.refresh(svc)

    now = datetime.utcnow()

    # 4x healthcheck failures
    for i in range(4):
        session.add(
            RestartEvent(
                service_id=svc.id,
                service_name="api",
                restarted_at=now - timedelta(hours=i + 1),
                reason="3_failed_health_checks",
                previous_status="unhealthy",
                new_status="restarting",
            )
        )

    # 2x manual restarts
    for i in range(2):
        session.add(
            RestartEvent(
                service_id=svc.id,
                service_name="api",
                restarted_at=now - timedelta(hours=i + 5),
                reason="manual_restart",
                previous_status="healthy",
                new_status="restarting",
            )
        )

    # 1x crash loop (outside 24h window — should NOT appear in 24h queries)
    session.add(
        RestartEvent(
            service_id=svc.id,
            service_name="api",
            restarted_at=now - timedelta(hours=30),
            reason="crash_loop",
            previous_status="unhealthy",
            new_status="restarting",
        )
    )

    session.commit()
    return svc


# ------------------------------------------------
# Tests: failure reason grouping
# ------------------------------------------------

def test_failure_reasons_breakdown_counts(seeded_service, engine):
    """
    get_failure_reasons_breakdown() should return correct counts
    grouped by reason within the default 24h window.
    The crash_loop event (30h ago) must NOT appear.
    """
    with patch("dockfleet.health.queries.engine", engine):
        breakdown = get_failure_reasons_breakdown("api", window_hours=24)

    assert breakdown["3_failed_health_checks"] == 4
    assert breakdown["manual_restart"] == 2
    # crash_loop is outside 24h window — must not appear
    assert "crash_loop" not in breakdown


def test_failure_reasons_breakdown_empty_for_unknown_service(engine):
    """
    get_failure_reasons_breakdown() should return an empty dict
    for a service that does not exist in the DB.
    """
    with patch("dockfleet.health.queries.engine", engine):
        breakdown = get_failure_reasons_breakdown("nonexistent", window_hours=24)

    assert breakdown == {}


def test_failure_reasons_breakdown_wider_window_includes_old_events(seeded_service, engine):
    """
    With a 48h window, the crash_loop event (30h ago) should now appear.
    """
    with patch("dockfleet.health.queries.engine", engine):
        breakdown = get_failure_reasons_breakdown("api", window_hours=48)

    assert breakdown["crash_loop"] == 1
    assert breakdown["3_failed_health_checks"] == 4


# ------------------------------------------------
# Tests: most unstable services
# ------------------------------------------------

def test_most_unstable_services_ordering(seeded_service, engine):
    """
    get_most_unstable_services() should return services ordered
    by restart count descending within the time window.
    """
    with patch("dockfleet.health.queries.engine", engine):
        result = get_most_unstable_services(limit=5, window_hours=24)

    # api has 6 events in last 24h (4 healthcheck + 2 manual)
    assert len(result) == 1
    assert result[0]["service_name"] == "api"
    assert result[0]["restarts"] == 6


def test_most_unstable_services_empty_when_no_events(engine):
    """
    get_most_unstable_services() should return empty list
    when there are no restart events in the window.
    """
    with patch("dockfleet.health.queries.engine", engine):
        result = get_most_unstable_services(limit=5, window_hours=24)

    assert result == []


# ------------------------------------------------
# Tests: restart history
# ------------------------------------------------

def test_restart_history_returns_events_in_window(seeded_service, engine):
    """
    get_restart_history() should return events within the since window,
    most recent first.
    """
    since = datetime.utcnow() - timedelta(hours=24)

    with patch("dockfleet.health.queries.engine", engine):
        history = get_restart_history("api", since=since)

    # 6 events within 24h (crash_loop is 30h ago)
    assert len(history) == 6

    # Each item must have expected keys
    for item in history:
        assert "timestamp" in item
        assert "reason" in item
        assert "previous_status" in item
        assert "new_status" in item


def test_restart_history_empty_for_unknown_service(engine):
    """
    get_restart_history() should return empty list for unknown service.
    """
    with patch("dockfleet.health.queries.engine", engine):
        history = get_restart_history("ghost_service")

    assert history == []
