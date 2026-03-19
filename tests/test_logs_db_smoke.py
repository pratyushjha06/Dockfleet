# tests/test_logs_db_smoke.py
"""
Smoke test for log aggregation API logic.

Covers:
- Seeding LogEvent rows
- Calling the /logs/db handler directly
- Filtering by service_name + q substring
- Basic shape of returned items
"""

from datetime import datetime, timedelta

from sqlmodel import Session

from dockfleet.health.models import Service, LogEvent, engine, init_db
from dockfleet.dashboard.routes import list_logs


def setup_function(_func):
    """
    Fresh DB + seed a few services/logs before each smoke test.
    """
    init_db()
    with Session(engine) as session:
        # ensure tables exist and clear them
        session.query(LogEvent).all()
        session.query(Service).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        session.commit()

    _seed_logs()


def _seed_logs():
    """
    Create two services (api, worker) and a few LogEvent rows
    with different messages/levels.
    """
    now = datetime.utcnow()

    with Session(engine) as session:
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

        logs = [
            LogEvent(
                service_id=api.id,
                service_name="api",
                created_at=now - timedelta(seconds=3),
                level="INFO",
                message="api started successfully",
                source="orchestrator",
            ),
            LogEvent(
                service_id=api.id,
                service_name="api",
                created_at=now - timedelta(seconds=2),
                level="ERROR",
                message="api request failed with 500 error",
                source="docker-logs",
            ),
            LogEvent(
                service_id=worker.id,
                service_name="worker",
                created_at=now - timedelta(seconds=1),
                level="WARN",
                message="worker slow job warning",
                source="scheduler",
            ),
        ]
        session.add_all(logs)
        session.commit()


def test_logs_db_filter_by_service_and_query():
    """
    Smoke: list_logs should filter by service_name and q substring,
    and return expected fields.
    """
    # This mirrors GET /logs/db?service_name=api&q=error&limit=10
    data = list_logs(service_name="api", q="error", limit=10, offset=0)

    # We expect exactly the one api log that contains "error"
    assert len(data) == 1

    log = data[0]
    # Basic shape checks
    assert "id" in log
    assert log["service_name"] == "api"
    assert "timestamp" in log
    assert log["level"] == "ERROR"
    assert "error" in (log["message"] or "").lower()
    assert log["source"] == "docker-logs"