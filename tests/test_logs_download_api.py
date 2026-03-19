# tests/test_logs_download_api.py

from datetime import datetime, timedelta

from sqlmodel import Session

from dockfleet.health.models import Service, LogEvent, engine, init_db
from dockfleet.health.logs import (
    query_logs,
    iter_logs_as_text,
    iter_logs_as_csv,
)


def setup_function(_func):
    # Fresh tables + seed for each test
    init_db()
    with Session(engine) as session:
        session.query(LogEvent).all()
        session.query(Service).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        session.commit()

    _seed_logs()


def _seed_logs():
    with Session(engine) as session:
        svc = Service(
            name="api",
            image="dummy-image",
            restart_policy="always",
        )
        session.add(svc)
        session.commit()
        session.refresh(svc)

        now = datetime.utcnow()
        logs = [
            LogEvent(
                service_id=svc.id,
                service_name="api",
                created_at=now - timedelta(seconds=3),
                level="INFO",
                message="api started",
                source="orchestrator",
            ),
            LogEvent(
                service_id=svc.id,
                service_name="api",
                created_at=now - timedelta(seconds=2),
                level="ERROR",
                message="request timeout error",
                source="docker-logs",
            ),
            LogEvent(
                service_id=svc.id,
                service_name="api",
                created_at=now - timedelta(seconds=1),
                level="WARN",
                message="slow response warning",
                source="scheduler",
            ),
        ]
        session.add_all(logs)
        session.commit()


def test_query_logs_basic_and_filtering():
    # basic fetch
    rows = query_logs()
    assert len(rows) == 3
    # newest first
    assert rows[0].message == "slow response warning"

    # filter by service_name
    rows = query_logs(service_name="api")
    assert len(rows) == 3
    assert all(r.service_name == "api" for r in rows)

    # substring search
    rows = query_logs(q="timeout")
    assert len(rows) == 1
    assert rows[0].message == "request timeout error"


def test_query_logs_pagination():
    # limit 1, offset 0
    rows_0 = query_logs(limit=1, offset=0)
    assert len(rows_0) == 1
    first = rows_0[0].message

    # limit 1, offset 1
    rows_1 = query_logs(limit=1, offset=1)
    assert len(rows_1) == 1
    second = rows_1[0].message

    assert first != second
    assert {first, second} <= {
        "slow response warning",
        "request timeout error",
        "api started",
    }


def test_iter_logs_as_text():
    lines = list(iter_logs_as_text(service_name="api", q=None, batch_size=2))
    # join for simple asserts
    body = "".join(lines)

    assert "api started" in body
    assert "request timeout error" in body
    assert "slow response warning" in body
    # basic format check
    assert "[api]" in body


def test_iter_logs_as_csv():
    chunks = list(iter_logs_as_csv(service_name="api", q=None, batch_size=2))
    body = "".join(chunks)

    lines = [line for line in body.splitlines() if line.strip()]
    # header row
    assert lines[0] == "service_name,timestamp,level,message,source"

    # at least three data rows
    assert len(lines) >= 4

    csv_body = "\n".join(lines[1:])
    assert "api" in csv_body
    assert "request timeout error" in csv_body
