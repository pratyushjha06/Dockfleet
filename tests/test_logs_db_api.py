from sqlmodel import Session, select
from dockfleet.health.models import Service, LogEvent, engine, init_db
from dockfleet.health.logs import store_log_line


def setup_function(_func):
    # Fresh tables for each test (simple version)
    init_db()
    with Session(engine) as session:
        session.exec(select(LogEvent)).all()  # ensure table exists
        session.exec(select(Service)).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        session.commit()


def test_store_log_and_filter_by_service():
    # Arrange: create one service
    with Session(engine) as session:
        svc = Service(
            name="api",
            image="dummy-image",
            restart_policy="always",
        )
        session.add(svc)
        session.commit()

    # Act: store two log lines
    store_log_line("api", "Service started", level="INFO", source="test")
    store_log_line("api", "Request failed with 500", level="ERROR", source="test")

    # Assert: logs present and filterable by service_name
    with Session(engine) as session:
        rows = (
            session.query(LogEvent)
            .filter(LogEvent.service_name == "api")
            .all()
        )

    assert len(rows) >= 2
    messages = [row.message for row in rows]
    assert any("Service started" in m for m in messages)
    assert any("Request failed" in m for m in messages)


def test_store_log_skips_unknown_service():
    # Act: call store_log_line with unknown service
    store_log_line("unknown-service", "Should not be stored", level="INFO", source="test")

    # Assert: no LogEvent rows for that name
    with Session(engine) as session:
        rows = (
            session.query(LogEvent)
            .filter(LogEvent.service_name == "unknown-service")
            .all()
        )

    assert len(rows) == 0
