from sqlmodel import select

from dockfleet.health.logs import store_log_line
from dockfleet.health.models import LogEvent, Service, get_session, init_db


def setup_function(_func):
    # Fresh tables for each test (simple version)
    init_db()
    with get_session() as session:
        session.exec(select(LogEvent)).all()  # ensure table exists
        session.exec(select(Service)).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        session.commit()


def test_store_log_and_filter_by_service():
    # Arrange: create one service
    with get_session() as session:
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
    with get_session() as session:
        rows = session.query(LogEvent).filter(LogEvent.service_name == "api").all()

    assert len(rows) >= 2
    messages = [row.message for row in rows]
    assert any("Service started" in m for m in messages)
    assert any("Request failed" in m for m in messages)


def test_store_log_skips_unknown_service():
    # Act: call store_log_line with unknown service
    store_log_line(
        "unknown-service", "Should not be stored", level="INFO", source="test"
    )

    # Assert: no LogEvent rows for that name
    with get_session() as session:
        rows = (
            session.query(LogEvent)
            .filter(LogEvent.service_name == "unknown-service")
            .all()
        )

    assert len(rows) == 0


def test_ingest_docker_logs_once_initial_and_incremental(monkeypatch):
    from unittest.mock import MagicMock, patch

    from dockfleet.health.log_ingestor import ingest_docker_logs_once

    with get_session() as session:
        svc = Service(
            name="api",
            image="dummy-image",
            restart_policy="always",
        )
        session.add(svc)
        session.commit()

    recorded_cmds = []

    def mock_subprocess_popen(cmd, *args, **kwargs):
        recorded_cmds.append(cmd)
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_stdout = MagicMock()
        
        if "--tail" in cmd:
            mock_stdout.__iter__.return_value = ["2026-10-02T22:21:32.123456789Z line 1\n", "2026-10-02T22:21:33.123456789Z line 2\n"]
        elif "--since" in cmd:
            mock_stdout.__iter__.return_value = ["2026-10-02T22:21:34.123456789Z line 3\n"]
        else:
            mock_stdout.__iter__.return_value = []
            
        mock_process.stdout = mock_stdout
        return mock_process

    with patch("subprocess.Popen", side_effect=mock_subprocess_popen):
        # 1. Initial ingest (no prior logs) -> should use --tail
        ingest_docker_logs_once(tail=200)

        with get_session() as session:
            rows = session.exec(
                select(LogEvent).where(LogEvent.service_name == "api")
            ).all()
            assert len(rows) == 2
            messages = [r.message for r in rows]
            assert messages == ["line 1", "line 2"]

        assert len(recorded_cmds) == 1
        assert recorded_cmds[0][:5] == ["docker", "logs", "--timestamps", "--tail", "200"]
        assert recorded_cmds[0][-1] == "dockfleet_api"

        # 2. Subsequent ingest -> should use --since with latest_ts isoformat
        ingest_docker_logs_once(tail=200)

        with get_session() as session:
            rows = session.exec(
                select(LogEvent)
                .where(LogEvent.service_name == "api")
                .order_by(LogEvent.created_at)
            ).all()
            assert len(rows) == 3
            messages = [r.message for r in rows]
            assert messages == ["line 1", "line 2", "line 3"]

        assert len(recorded_cmds) == 2
        assert recorded_cmds[1][:3] == ["docker", "logs", "--timestamps"]
        assert recorded_cmds[1][3] == "--since"
        assert recorded_cmds[1][-1] == "dockfleet_api"

def test_ingest_docker_logs_batching():
    from unittest.mock import MagicMock, patch

    from sqlmodel import Session

    from dockfleet.health.log_ingestor import ingest_docker_logs_once

    with get_session() as session:
        session.exec(select(LogEvent)).all()
        session.exec(select(Service)).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        
        svc = Service(
            name="api",
            image="dummy-image",
            restart_policy="always",
        )
        session.add(svc)
        session.commit()

    def mock_subprocess_popen(cmd, *args, **kwargs):
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_stdout = MagicMock()
        mock_stdout.__iter__.return_value = [f"2026-10-02T22:21:32.123456789Z line {i}\n" for i in range(2001)]
        mock_process.stdout = mock_stdout
        return mock_process

    original_commit = Session.commit

    with patch("subprocess.Popen", side_effect=mock_subprocess_popen):
        with patch.object(Session, "commit", autospec=True, side_effect=original_commit) as mock_commit:
            ingest_docker_logs_once(tail=2001)

            # 2001 logs -> two 1,000-event batch commits + one 1-event remainder commit
            # (there may also be an outer commit from the session context manager, so >= 3)
            assert mock_commit.call_count >= 3

        with get_session() as session:
            rows = session.exec(select(LogEvent)).all()
            assert len(rows) == 2001


def test_ingest_docker_logs_nonzero_exit_discards_streamed_output():
    from unittest.mock import MagicMock, patch

    from sqlmodel import select

    from dockfleet.health.log_ingestor import ingest_docker_logs_once

    with get_session() as session:
        session.exec(select(LogEvent)).all()
        session.exec(select(Service)).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        session.add(
            Service(
                name="api",
                image="dummy-image",
                restart_policy="always",
            )
        )
        session.commit()

    mock_process = MagicMock()
    mock_process.wait.return_value = 1
    mock_process.stdout = MagicMock()
    mock_process.stdout.__iter__.return_value = ["2026-10-02T22:21:32.123456789Z partial line\n"]

    with patch("subprocess.Popen", return_value=mock_process):
        ingest_docker_logs_once(tail=200)

    with get_session() as session:
        rows = session.exec(select(LogEvent)).all()
        assert rows == []


def test_ingest_docker_logs_preserves_docker_timestamps_without_skew():
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    from sqlmodel import select

    from dockfleet.health.log_ingestor import ingest_docker_logs_once

    with get_session() as session:
        session.exec(select(LogEvent)).all()
        session.exec(select(Service)).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        svc = Service(
            name="worker",
            image="dummy-image",
            restart_policy="always",
        )
        session.add(svc)
        session.commit()

    docker_timestamps = [
        "2026-01-15T10:00:00.100000000Z",
        "2026-01-15T10:00:00.200000000Z",
        "2026-01-15T10:00:01.000000000Z",
        "2026-01-15T10:00:05.500000000Z",
    ]

    mock_process = MagicMock()
    mock_process.wait.return_value = 0
    mock_process.stdout = MagicMock()
    mock_process.stdout.__iter__.return_value = [
        f"{ts} log entry {i}\n" for i, ts in enumerate(docker_timestamps)
    ]

    with patch("subprocess.Popen", return_value=mock_process):
        ingest_docker_logs_once(tail=200)

    with get_session() as session:
        rows = session.exec(
            select(LogEvent)
            .where(LogEvent.service_name == "worker")
            .order_by(LogEvent.id)
        ).all()
        assert len(rows) == 4
        # Verify created_at accurately reflects the actual Docker log timestamps
        assert rows[0].created_at == datetime(2026, 1, 15, 10, 0, 0, 100000, tzinfo=timezone.utc).replace(tzinfo=None) or rows[0].created_at == datetime(2026, 1, 15, 10, 0, 0, 100000, tzinfo=timezone.utc)
        assert rows[1].created_at == datetime(2026, 1, 15, 10, 0, 0, 200000, tzinfo=timezone.utc).replace(tzinfo=None) or rows[1].created_at == datetime(2026, 1, 15, 10, 0, 0, 200000, tzinfo=timezone.utc)
        assert rows[2].created_at == datetime(2026, 1, 15, 10, 0, 1, 0, tzinfo=timezone.utc).replace(tzinfo=None) or rows[2].created_at == datetime(2026, 1, 15, 10, 0, 1, 0, tzinfo=timezone.utc)
        assert rows[3].created_at == datetime(2026, 1, 15, 10, 0, 5, 500000, tzinfo=timezone.utc).replace(tzinfo=None) or rows[3].created_at == datetime(2026, 1, 15, 10, 0, 5, 500000, tzinfo=timezone.utc)


def test_ingest_docker_logs_boundary_deduplication():
    from unittest.mock import MagicMock, patch
    from dockfleet.health.log_ingestor import ingest_docker_logs_once

    with get_session() as session:
        session.exec(select(LogEvent)).all()
        session.exec(select(Service)).all()
        session.query(LogEvent).delete()
        session.query(Service).delete()
        svc = Service(
            name="api",
            image="dummy-image",
            restart_policy="always",
        )
        session.add(svc)
        session.commit()

    cycle = 1

    def mock_subprocess_popen(cmd, *args, **kwargs):
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_stdout = MagicMock()

        if cycle == 1:
            mock_stdout.__iter__.return_value = [
                "2026-10-02T22:21:32.100000000Z line 1\n",
                "2026-10-02T22:21:33.200000000Z line 2\n",
            ]
        elif cycle == 2:
            # Docker returns inclusive boundary record (line 2) + new record (line 3)
            mock_stdout.__iter__.return_value = [
                "2026-10-02T22:21:33.200000000Z line 2\n",
                "2026-10-02T22:21:34.300000000Z line 3\n",
            ]
        elif cycle == 3:
            # Docker returns only boundary record (line 3) when no new logs occurred
            mock_stdout.__iter__.return_value = [
                "2026-10-02T22:21:34.300000000Z line 3\n",
            ]
        else:
            mock_stdout.__iter__.return_value = []

        mock_process.stdout = mock_stdout
        return mock_process

    with patch("subprocess.Popen", side_effect=mock_subprocess_popen):
        # Cycle 1: Initial ingest
        ingest_docker_logs_once(tail=200)
        with get_session() as session:
            rows = session.exec(select(LogEvent).where(LogEvent.service_name == "api")).all()
            assert len(rows) == 2
            assert [r.message for r in rows] == ["line 1", "line 2"]

        # Cycle 2: Incremental ingest with boundary record repetition
        cycle = 2
        ingest_docker_logs_once(tail=200)
        with get_session() as session:
            rows = session.exec(
                select(LogEvent)
                .where(LogEvent.service_name == "api")
                .order_by(LogEvent.id)
            ).all()
            assert len(rows) == 3
            assert [r.message for r in rows] == ["line 1", "line 2", "line 3"]

        # Cycle 3: Incremental ingest with only boundary record (no new events)
        cycle = 3
        ingest_docker_logs_once(tail=200)
        with get_session() as session:
            rows = session.exec(
                select(LogEvent)
                .where(LogEvent.service_name == "api")
                .order_by(LogEvent.id)
            ).all()
            assert len(rows) == 3
            assert [r.message for r in rows] == ["line 1", "line 2", "line 3"]


def test_parse_docker_timestamp_formats():
    from datetime import datetime, timezone
    from dockfleet.health.log_ingestor import _parse_docker_timestamp

    # Nanosecond Z
    dt = _parse_docker_timestamp("2026-10-02T22:21:32.123456789Z")
    assert dt == datetime(2026, 10, 2, 22, 21, 32, 123456, tzinfo=timezone.utc)

    # Microsecond Z
    dt = _parse_docker_timestamp("2026-10-02T22:21:32.123456Z")
    assert dt == datetime(2026, 10, 2, 22, 21, 32, 123456, tzinfo=timezone.utc)

    # Second precision Z
    dt = _parse_docker_timestamp("2026-10-02T22:21:32Z")
    assert dt == datetime(2026, 10, 2, 22, 21, 32, 0, tzinfo=timezone.utc)

    # Space separator
    dt = _parse_docker_timestamp("2026-10-02 22:21:32.123456Z")
    assert dt == datetime(2026, 10, 2, 22, 21, 32, 123456, tzinfo=timezone.utc)

    # Offset timezone converted to UTC
    dt = _parse_docker_timestamp("2026-10-02T22:21:32.000000+02:00")
    assert dt == datetime(2026, 10, 2, 20, 21, 32, 0, tzinfo=timezone.utc)


def test_normalize_utc_datetime():
    from datetime import datetime, timezone
    from dockfleet.health.log_ingestor import _normalize_utc_datetime

    # 1. Naive datetime -> converted to UTC aware
    naive_dt = datetime(2026, 10, 2, 12, 0, 0)
    norm_naive = _normalize_utc_datetime(naive_dt)
    assert norm_naive is not None
    assert norm_naive.tzinfo == timezone.utc
    assert norm_naive == datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    # 2. Timezone-aware datetime -> converted to UTC
    aware_dt = datetime(2026, 10, 2, 14, 0, 0, tzinfo=timezone.utc)
    norm_aware = _normalize_utc_datetime(aware_dt)
    assert norm_aware == aware_dt

    # 3. String representation
    norm_str = _normalize_utc_datetime("2026-10-02T12:00:00Z")
    assert norm_str == datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    # 4. None / invalid
    assert _normalize_utc_datetime(None) is None
    assert _normalize_utc_datetime("not-a-date") is None



