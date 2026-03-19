# tests/test_crash_analytics_queries.py

from datetime import datetime, timedelta

from sqlmodel import Session

from dockfleet.health.models import Service, RestartEvent, engine, init_db
from dockfleet.health.queries import (
    get_restart_history,
    get_most_unstable_services,
    get_failure_reasons_breakdown,
)


def setup_function(_func):
    # Fresh DB state before each test
    init_db()
    with Session(engine) as session:
        # ensure tables exist and clear them
        session.query(RestartEvent).all()
        session.query(Service).all()
        session.query(RestartEvent).delete()
        session.query(Service).delete()
        session.commit()

    _seed_restarts()


def _seed_restarts():
    with Session(engine) as session:
        now = datetime.utcnow()

        api = Service(
            name="api",
            image="api:latest",
            restart_policy="always",
        )
        worker = Service(
            name="worker",
            image="worker:latest",
            restart_policy="always",
        )
        session.add(api)
        session.add(worker)
        session.commit()
        session.refresh(api)
        session.refresh(worker)

        events = [
            # api: 3 restarts with different reasons/times
            RestartEvent(
                service_id=api.id,
                service_name="api",
                restarted_at=now - timedelta(hours=1),
                reason="3_failed_health_checks",
                previous_status="unhealthy",
                new_status="running",
            ),
            RestartEvent(
                service_id=api.id,
                service_name="api",
                restarted_at=now - timedelta(minutes=30),
                reason="3_failed_health_checks",
                previous_status="unhealthy",
                new_status="running",
            ),
            RestartEvent(
                service_id=api.id,
                service_name="api",
                restarted_at=now - timedelta(minutes=10),
                reason="manual_dashboard_restart",
                previous_status="running",
                new_status="running",
            ),
            # worker: 1 restart
            RestartEvent(
                service_id=worker.id,
                service_name="worker",
                restarted_at=now - timedelta(minutes=5),
                reason="3_failed_health_checks",
                previous_status="unhealthy",
                new_status="running",
            ),
        ]
        session.add_all(events)
        session.commit()


def test_get_restart_history():
    history = get_restart_history("api")
    assert len(history) == 3

    # sorted desc by timestamp
    assert history[0]["timestamp"] >= history[-1]["timestamp"]

    reasons = {h["reason"] for h in history}
    assert "3_failed_health_checks" in reasons
    assert "manual_dashboard_restart" in reasons


def test_get_most_unstable_services():
    unstable = get_most_unstable_services(limit=2, window_hours=24)
    assert len(unstable) >= 2

    # api should come before worker (3 vs 1 restarts)
    assert unstable[0]["service_name"] == "api"
    assert unstable[0]["restarts"] == 3

    names = {row["service_name"] for row in unstable}
    assert {"api", "worker"} <= names


def test_get_failure_reasons_breakdown():
    breakdown = get_failure_reasons_breakdown("api", window_hours=24)
    # api: 2 health_check + 1 manual
    assert breakdown["3_failed_health_checks"] == 2
    assert breakdown["manual_dashboard_restart"] == 1

    # worker ke liye bhi sanity check
    worker_breakdown = get_failure_reasons_breakdown("worker", window_hours=24)
    assert worker_breakdown["3_failed_health_checks"] == 1
