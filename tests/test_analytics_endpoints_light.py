# tests/test_analytics_endpoints_light.py

from datetime import datetime, timedelta

from sqlmodel import Session

from dockfleet.health.models import Service, RestartEvent, engine, init_db
from dockfleet.dashboard.routes import (
    analytics_unstable_services,
    analytics_restart_history,
    analytics_failure_reasons,
)


def setup_function(_func):
    # Fresh DB state before each test
    init_db()
    with Session(engine) as session:
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
        session.add_all([api, worker])
        session.commit()
        session.refresh(api)
        session.refresh(worker)

        events = [
            # api: 2 restarts
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


def test_analytics_unstable_services_basic():
    res = analytics_unstable_services(limit=2, window_hours=24)
    # should return at least api and worker
    assert len(res) >= 2

    # api should be most unstable (2 restarts)
    assert res[0].service_name == "api"
    assert res[0].restarts == 2
    assert res[0].last_restart_at is not None


def test_analytics_restart_history_endpoint():
    res = analytics_restart_history("api", since_hours=24)
    assert len(res) == 2
    reasons = {r.reason for r in res}
    assert "3_failed_health_checks" in reasons
    assert "manual_dashboard_restart" in reasons


def test_analytics_failure_reasons_endpoint():
    res = analytics_failure_reasons("api", window_hours=24)
    # convert to dict for easy assertions
    reasons = {item.reason: item.count for item in res}
    assert reasons["3_failed_health_checks"] == 1
    assert reasons["manual_dashboard_restart"] == 1

    worker_res = analytics_failure_reasons("worker", window_hours=24)
    worker_reasons = {item.reason: item.count for item in worker_res}
    assert worker_reasons["3_failed_health_checks"] == 1
