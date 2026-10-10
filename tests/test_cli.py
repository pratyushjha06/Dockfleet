from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from dockfleet.cli.main import app

runner = CliRunner()


def test_cli_validate_success():
    result = runner.invoke(app, ["validate", "examples/dockfleet.yaml"])
    assert result.exit_code == 0
    assert "Config valid" in result.stdout


def test_cli_validate_invalid_schema(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("""
services:
  web:
    image: nginx
    resources:
      cpu: -1.0
""")
    result = runner.invoke(app, ["validate", str(bad_config)])
    assert result.exit_code == 1
    assert "Unexpected error:" not in result.stdout
    assert "Configuration Validation Error" in result.output


def test_cli_validate_out_of_range_port(tmp_path):
    bad_config = tmp_path / "bad_port.yaml"
    bad_config.write_text("""
services:
  web:
    image: nginx
    restart: always
    ports:
      - "80:70000"
""")
    result = runner.invoke(app, ["validate", str(bad_config)])
    assert result.exit_code == 1
    assert "Configuration Validation Error" in result.output
    assert "Port values must be between 1 and 65535" in result.output


def test_cli_validate_non_positive_healthcheck_interval(tmp_path):
    bad_config = tmp_path / "bad_interval.yaml"
    bad_config.write_text("""
services:
  web:
    image: nginx
    restart: always
    healthcheck:
      type: http
      endpoint: "http://localhost:80/health"
      interval: 0
""")
    result = runner.invoke(app, ["validate", str(bad_config)])
    assert result.exit_code == 1
    assert "Configuration Validation Error" in result.output
    assert "interval must be greater than 0" in result.output

    bad_config_neg = tmp_path / "bad_interval_neg.yaml"
    bad_config_neg.write_text("""
services:
  web:
    image: nginx
    restart: always
    healthcheck:
      type: http
      endpoint: "http://localhost:80/health"
      interval: -5
""")
    result_neg = runner.invoke(app, ["validate", str(bad_config_neg)])
    assert result_neg.exit_code == 1
    assert "Configuration Validation Error" in result_neg.output
    assert "interval must be greater than 0" in result_neg.output




@patch("dockfleet.cli.main.spawn_background_scheduler")
@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.stop_background_scheduler")
@patch("dockfleet.cli.main.Orchestrator.restart")
def test_cli_restart(mock_restart, mock_stop_scheduler, mock_bootstrap, mock_spawn_scheduler):
    """Test that the restart command stops scheduler, bootstraps DB, restarts orchestrator, and respawns scheduler."""
    result = runner.invoke(app, ["restart", "examples/dockfleet.yaml"])
    assert result.exit_code == 0
    assert "Restarting services from" in result.stdout
    assert "Health scheduler started in background" in result.stdout
    mock_stop_scheduler.assert_called_once()
    mock_bootstrap.assert_called_once_with(str(Path("examples/dockfleet.yaml")))
    mock_restart.assert_called_once()
    mock_spawn_scheduler.assert_called_once_with(str(Path("examples/dockfleet.yaml")))


@patch("dockfleet.cli.main.spawn_background_scheduler")
@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.stop_background_scheduler")
@patch("dockfleet.cli.main.Orchestrator.restart")
def test_cli_restart_failure(mock_restart, mock_stop_scheduler, mock_bootstrap, mock_spawn_scheduler):
    """Test that the restart command handles and exits with code 1."""
    mock_restart.side_effect = RuntimeError("Failed to stop services")
    result = runner.invoke(app, ["restart", "examples/dockfleet.yaml"])
    assert result.exit_code == 1
    assert "Error restarting services" in result.stdout


@patch("dockfleet.cli.main.spawn_background_scheduler")
@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.stop_background_scheduler")
@patch("dockfleet.core.orchestrator.mark_service_stopped")
@patch("dockfleet.core.docker.DockerManager.remove_container")
@patch("dockfleet.core.docker.DockerManager.stop_container")
@patch("dockfleet.core.orchestrator.Orchestrator.up")
def test_cli_restart_absent_container(mock_up, mock_stop, mock_remove, mock_mark, mock_stop_scheduler, mock_bootstrap, mock_spawn_scheduler):
    """Regression test: restart proceeds when the configured container does not exist."""
    # Simulate Docker throwing a "No such container" error during down()
    mock_stop.side_effect = Exception("Error: No such container: dockfleet_api")

    # Run the restart command
    result = runner.invoke(app, ["restart", "examples/dockfleet.yaml"])

    # Ensure it didn't crash and successfully reached up()
    assert result.exit_code == 0
    mock_up.assert_called_once()


@patch("dockfleet.cli.main.importlib.metadata.version")
def test_cli_version(mock_version):
    """Test that the --version option outputs the version and exits."""
    mock_version.return_value = "1.2.3"
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "DockFleet version 1.2.3" in result.stdout


@patch("dockfleet.cli.main.spawn_background_scheduler")
@patch("dockfleet.cli.main.SchedulerLock._pid_is_running", return_value=False)
@patch("dockfleet.cli.main.SchedulerLock._read_pid_file", return_value=None)
@patch("dockfleet.core.orchestrator.Orchestrator.up")
@patch("dockfleet.cli.main.bootstrap_from_path")
def test_cli_up_detached(mock_bootstrap, mock_up, mock_read_pid, mock_pid_running, mock_spawn):
    """Test that dockfleet up in default detached mode launches background scheduler."""
    result = runner.invoke(app, ["up", "examples/dockfleet.yaml"])
    assert result.exit_code == 0
    assert "Starting services from" in result.stdout
    assert "Health scheduler started in background" in result.stdout
    mock_up.assert_called_once()
    mock_spawn.assert_called_once_with(str(Path("examples/dockfleet.yaml")))


@patch("dockfleet.cli.main.spawn_background_scheduler")
@patch("dockfleet.cli.main.SchedulerLock._pid_is_running", return_value=True)
@patch("dockfleet.cli.main.SchedulerLock._read_pid_file", return_value={"pid": 12345})
@patch("dockfleet.core.orchestrator.Orchestrator.up")
@patch("dockfleet.cli.main.bootstrap_from_path")
def test_cli_up_detached_already_running(mock_bootstrap, mock_up, mock_read_pid, mock_pid_running, mock_spawn):
    """Test that dockfleet up detects an existing running scheduler and does not spawn another."""
    result = runner.invoke(app, ["up", "examples/dockfleet.yaml"])
    assert result.exit_code == 0
    assert "Health scheduler is already running in background (PID 12345)" in result.stdout
    mock_up.assert_called_once()
    mock_spawn.assert_not_called()


@patch("dockfleet.cli.main.HealthScheduler")
@patch("dockfleet.core.orchestrator.Orchestrator.up")
@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.time.sleep", side_effect=KeyboardInterrupt)
def test_cli_up_foreground(mock_sleep, mock_bootstrap, mock_up, mock_scheduler_cls):
    """Test that dockfleet up --foreground runs scheduler in foreground until interrupted."""
    mock_scheduler = mock_scheduler_cls.return_value
    result = runner.invoke(app, ["up", "examples/dockfleet.yaml", "--foreground"])
    assert result.exit_code == 0
    assert "Running health scheduler in foreground" in result.stdout
    assert "Stopping health scheduler..." in result.stdout
    mock_up.assert_called_once()
    mock_scheduler.start.assert_called_once()
    mock_scheduler.stop.assert_called_once()


@patch("dockfleet.cli.main.stop_background_scheduler")
@patch("dockfleet.core.orchestrator.Orchestrator.down")
def test_cli_down_stops_scheduler(mock_down, mock_stop_scheduler):
    """Test that dockfleet down stops orchestrator services and any background scheduler."""
    result = runner.invoke(app, ["down", "examples/dockfleet.yaml"])
    assert result.exit_code == 0
    assert "Stopping services from" in result.stdout
    assert "Services stopped" in result.stdout
    mock_down.assert_called_once()
    mock_stop_scheduler.assert_called_once()


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_missing_container_follow(mock_run):
    """Test that dockfleet logs --follow outputs error and exits 1 when container is missing."""
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(returncode=1, stderr="Error: No such container: dockfleet_invalid_service\n")
    result = runner.invoke(app, ["logs", "invalid_service", "--follow"])
    assert result.exit_code == 1
    assert "Service 'invalid_service' not found or container not running." in result.stdout
    assert "Streaming logs" not in result.stdout


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_missing_container_no_follow(mock_run):
    """Test that dockfleet logs outputs error and exits 1 when container is missing."""
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(returncode=1, stderr="Error: No such container: dockfleet_invalid_service\n")
    result = runner.invoke(app, ["logs", "invalid_service"])
    assert result.exit_code == 1
    assert "Service 'invalid_service' not found or container not running." in result.stdout


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_success_follow(mock_run):
    """Test that dockfleet logs --follow streams logs when container exists."""
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(returncode=0, stdout="")
    result = runner.invoke(app, ["logs", "web", "--follow"])
    assert result.exit_code == 0
    assert "Streaming logs for web" in result.stdout


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_follow_keyboard_interrupt(mock_run):
    """Test that dockfleet logs --follow exits cleanly with code 0 on KeyboardInterrupt."""
    from unittest.mock import MagicMock

    mock_run.side_effect = [MagicMock(returncode=0), KeyboardInterrupt()]
    result = runner.invoke(app, ["logs", "web", "--follow"])
    assert result.exit_code == 0
    assert "Streaming logs for web" in result.stdout
    assert "Service 'web' not found or container not running." not in result.stdout


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_follow_interrupt_returncode_130(mock_run):
    """Test that dockfleet logs --follow exits cleanly with code 0 on SIGINT return code (130)."""
    from unittest.mock import MagicMock

    mock_run.side_effect = [MagicMock(returncode=0), MagicMock(returncode=130)]
    result = runner.invoke(app, ["logs", "web", "--follow"])
    assert result.exit_code == 0
    assert "Streaming logs for web" in result.stdout
    assert "Service 'web' not found or container not running." not in result.stdout


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_follow_interrupt_returncode_windows(mock_run):
    """Test that dockfleet logs --follow exits cleanly with code 0 on Windows Ctrl+C (0xC000013A)."""
    from unittest.mock import MagicMock

    mock_run.side_effect = [MagicMock(returncode=0), MagicMock(returncode=3221225786)]
    result = runner.invoke(app, ["logs", "web", "--follow"])
    assert result.exit_code == 0
    assert "Streaming logs for web" in result.stdout
    assert "Service 'web' not found or container not running." not in result.stdout


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_follow_interrupt_returncode_signal(mock_run):
    """Test that dockfleet logs --follow exits cleanly with code 0 on negative SIGINT return code."""
    from unittest.mock import MagicMock

    mock_run.side_effect = [MagicMock(returncode=0), MagicMock(returncode=-2)]
    result = runner.invoke(app, ["logs", "web", "--follow"])
    assert result.exit_code == 0
    assert "Streaming logs for web" in result.stdout
    assert "Service 'web' not found or container not running." not in result.stdout



@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_success_no_follow(mock_run):
    """Test that dockfleet logs outputs logs when container exists."""
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(returncode=0, stdout="Application started successfully\n")
    result = runner.invoke(app, ["logs", "web"])
    assert result.exit_code == 0
    assert "Application started successfully" in result.stdout
    # Verify subprocess.run was called with encoding="utf-8" and errors="replace"
    calls = mock_run.call_args_list
    assert len(calls) >= 2
    for call in calls:
        kwargs = call[1]
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_unicode_characters(mock_run):
    """Test that dockfleet logs handles UTF-8 characters such as emojis and symbols."""
    from unittest.mock import MagicMock

    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="🚀 Service started ✓ [日本語 logs: 起動完了]\n",
    )
    result = runner.invoke(app, ["logs", "web"])
    assert result.exit_code == 0
    assert "🚀 Service started ✓ [日本語 logs: 起動完了]" in result.stdout


@patch("dockfleet.cli.main.subprocess.run")
def test_cli_logs_bytes_with_non_utf8_fallback(mock_run):
    """Test that dockfleet logs handles raw bytes with invalid sequences via errors='replace'."""
    from unittest.mock import MagicMock

    # Raw bytes with invalid UTF-8 byte 0xFF
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),
        MagicMock(returncode=0, stdout=b"Corrupt byte \xff in stream\n", stderr=b""),
    ]
    result = runner.invoke(app, ["logs", "web"])
    assert result.exit_code == 0
    assert "Corrupt byte" in result.stdout
    assert "\ufffd" in result.stdout or "?" in result.stdout



def test_stop_background_scheduler_missing_pid_file(tmp_path):
    from dockfleet.cli.main import stop_background_scheduler

    assert stop_background_scheduler(tmp_path) is False


def test_stop_background_scheduler_dead_pid(tmp_path):
    import json
    from dockfleet.cli.main import stop_background_scheduler
    from dockfleet.health.scheduler_lock import SchedulerLock

    pid_file = tmp_path / SchedulerLock.PID_FILENAME
    lock_file = tmp_path / SchedulerLock.LOCK_FILENAME
    pid_file.write_text(json.dumps({"pid": 9999999}))
    lock_file.write_text("lock")
    assert stop_background_scheduler(tmp_path) is False
    assert not pid_file.exists()
    assert not lock_file.exists()


@patch("dockfleet.cli.main.SchedulerLock._pid_is_running", return_value=True)
@patch("dockfleet.cli.main.os.kill")
def test_stop_background_scheduler_posix(mock_kill, mock_pid_running, tmp_path, monkeypatch):
    import json
    import signal
    from dockfleet.cli.main import stop_background_scheduler
    from dockfleet.health.scheduler_lock import SchedulerLock

    monkeypatch.setattr("sys.platform", "linux")
    pid_file = tmp_path / SchedulerLock.PID_FILENAME
    lock_file = tmp_path / SchedulerLock.LOCK_FILENAME
    pid_file.write_text(json.dumps({"pid": 1234}))
    lock_file.write_text("lock")

    assert stop_background_scheduler(tmp_path) is True
    mock_kill.assert_called_once_with(1234, signal.SIGTERM)
    assert not pid_file.exists()
    assert not lock_file.exists()


@patch("dockfleet.cli.main.SchedulerLock._pid_is_running", return_value=True)
@patch("dockfleet.cli.main.os.kill")
def test_stop_background_scheduler_windows(mock_kill, mock_pid_running, tmp_path, monkeypatch):
    import json
    import signal
    from dockfleet.cli.main import stop_background_scheduler
    from dockfleet.health.scheduler_lock import SchedulerLock

    monkeypatch.setattr("sys.platform", "win32")
    pid_file = tmp_path / SchedulerLock.PID_FILENAME
    lock_file = tmp_path / SchedulerLock.LOCK_FILENAME
    pid_file.write_text(json.dumps({"pid": 1234}))
    lock_file.write_text("lock")

    assert stop_background_scheduler(tmp_path) is True
    mock_kill.assert_called_once_with(1234, signal.SIGTERM)
    assert not pid_file.exists()
    assert not lock_file.exists()


@patch("dockfleet.core.orchestrator.Orchestrator.down")
def test_cli_down_deletes_orphaned_lock_and_pid_files(mock_down, tmp_path):
    """Test that dockfleet down deletes .scheduler.lock and .scheduler.pid files in project directory."""
    import json
    from dockfleet.health.scheduler_lock import SchedulerLock

    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  api:
    image: nginx
    restart: always
""")

    pid_file = tmp_path / SchedulerLock.PID_FILENAME
    lock_file = tmp_path / SchedulerLock.LOCK_FILENAME
    pid_file.write_text(json.dumps({"pid": 9999999}))
    lock_file.write_text("lock")

    result = runner.invoke(app, ["down", str(config_file)])
    assert result.exit_code == 0
    assert not pid_file.exists()
    assert not lock_file.exists()


@patch("dockfleet.cli.main.SchedulerLock._pid_is_running", return_value=True)
@patch("dockfleet.cli.main.os.kill")
@patch("dockfleet.core.orchestrator.Orchestrator.down")
def test_cli_down_stops_scheduler_in_subdirectory(mock_down, mock_kill, mock_pid_running, tmp_path):
    """Test that dockfleet down on a subdirectory config path stops the running background scheduler and cleans up lock/pid files."""
    import json
    import signal
    from dockfleet.health.scheduler_lock import SchedulerLock

    sub_dir = tmp_path / "nested" / "project"
    sub_dir.mkdir(parents=True, exist_ok=True)
    config_file = sub_dir / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
""")

    pid_file = sub_dir / SchedulerLock.PID_FILENAME
    lock_file = sub_dir / SchedulerLock.LOCK_FILENAME
    pid_file.write_text(json.dumps({"pid": 4321}))
    lock_file.write_text("lock")

    result = runner.invoke(app, ["down", str(config_file)])
    assert result.exit_code == 0
    assert "Stopping services from" in result.stdout
    assert "Services stopped" in result.stdout
    mock_down.assert_called_once()
    mock_kill.assert_called_once_with(4321, signal.SIGTERM)
    assert not pid_file.exists()
    assert not lock_file.exists()



def test_cli_show_logs_displays_most_recent_ten_logs(tmp_path, monkeypatch):
    """Test that dockfleet show-logs displays the 10 most recent logs in descending order."""
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session, SQLModel, create_engine
    from dockfleet.health.models import LogEvent, Service, get_session

    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(test_engine)

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    with Session(test_engine) as session:
        svc = Service(name="api", image="nginx", restart_policy="always", restart_count=0)
        session.add(svc)
        session.commit()

        for i in range(20):
            event = LogEvent(
                service_id=svc.id,
                service_name="api",
                created_at=base_time + timedelta(minutes=i),
                message=f"Log message {i}",
            )
            session.add(event)
        session.commit()

    monkeypatch.setattr(
        "dockfleet.cli.main.get_session",
        lambda: get_session(engine=test_engine),
    )

    result = runner.invoke(app, ["show-logs"])
    assert result.exit_code == 0

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 10

    # Extract log message payload from lines: "[YYYY-MM-DD HH:MM:SS] [api] Log message X"
    extracted_messages = [l.split("] ", 2)[-1] for l in lines]
    expected_messages = [f"Log message {i}" for i in range(19, 9, -1)]
    assert extracted_messages == expected_messages


def test_cli_show_logs_service_filter_and_custom_limit(tmp_path, monkeypatch):
    """Test that dockfleet show-logs supports --service filter and custom --limit."""
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session, SQLModel, create_engine
    from dockfleet.health.models import LogEvent, Service, get_session

    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(test_engine)

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    with Session(test_engine) as session:
        svc1 = Service(name="api", image="nginx", restart_policy="always", restart_count=0)
        svc2 = Service(name="web", image="nginx", restart_policy="always", restart_count=0)
        session.add_all([svc1, svc2])
        session.commit()

        for i in range(10):
            session.add(LogEvent(service_id=svc1.id, service_name="api", created_at=base_time + timedelta(minutes=i), message=f"api log {i}"))
            session.add(LogEvent(service_id=svc2.id, service_name="web", created_at=base_time + timedelta(minutes=i), message=f"web log {i}"))
        session.commit()

    monkeypatch.setattr(
        "dockfleet.cli.main.get_session",
        lambda: get_session(engine=test_engine),
    )

    result = runner.invoke(app, ["show-logs", "--service", "web", "--limit", "3"])
    assert result.exit_code == 0

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 3
    assert "api log" not in result.stdout
    assert "web log 9" in lines[0]
    assert "web log 8" in lines[1]
    assert "web log 7" in lines[2]


@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.HealthScheduler")
def test_cli_health_dev_once_healthy(mock_scheduler_cls, mock_bootstrap):
    """Test that health-dev --once exits with code 0 when all services are healthy."""
    mock_scheduler = mock_scheduler_cls.return_value
    mock_scheduler.run_single_pass.return_value = {"db": True, "api": True, "crash_test": True}

    result = runner.invoke(app, ["health-dev", "examples/dockfleet.yaml", "--once"])
    assert result.exit_code == 0
    assert "Running a single health pass" in result.stdout
    assert "api: healthy" in result.stdout
    assert "db: healthy" in result.stdout
    assert "crash_test: healthy" in result.stdout
    assert "Single health pass complete." in result.stdout
    mock_scheduler.run_single_pass.assert_called_once()


@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.HealthScheduler")
def test_cli_health_dev_once_unhealthy(mock_scheduler_cls, mock_bootstrap):
    """Test that health-dev --once exits with code 1 when any service is unhealthy."""
    mock_scheduler = mock_scheduler_cls.return_value
    mock_scheduler.run_single_pass.return_value = {"api": False}

    result = runner.invoke(app, ["health-dev", "examples/dockfleet.yaml", "--once"])
    assert result.exit_code == 1
    assert "Running a single health pass" in result.stdout
    assert "api: unhealthy" in result.stdout
    assert "Single health pass complete." in result.stdout
    mock_scheduler.run_single_pass.assert_called_once()


@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.HealthScheduler")
def test_cli_health_dev_once_stopped_services(mock_scheduler_cls, mock_bootstrap):
    """Test that health-dev --once exits with code 0 when stopped services exist and active services are healthy."""
    mock_scheduler = mock_scheduler_cls.return_value
    mock_scheduler.run_single_pass.return_value = {"db": True}

    result = runner.invoke(app, ["health-dev", "examples/dockfleet.yaml", "--once"])
    assert result.exit_code == 0
    assert "Running a single health pass" in result.stdout
    assert "db: healthy" in result.stdout
    assert "Single health pass complete." in result.stdout
    mock_scheduler.run_single_pass.assert_called_once()


@patch("dockfleet.cli.main.bootstrap_from_path")
@patch("dockfleet.cli.main.HealthScheduler")
def test_cli_health_dev_once_all_stopped_services(mock_scheduler_cls, mock_bootstrap):
    """Test that health-dev --once exits with code 0 when all configured services are stopped."""
    mock_scheduler = mock_scheduler_cls.return_value
    mock_scheduler.run_single_pass.return_value = {}

    result = runner.invoke(app, ["health-dev", "examples/dockfleet.yaml", "--once"])
    assert result.exit_code == 0
    assert "Single health pass complete." in result.stdout
    mock_scheduler.run_single_pass.assert_called_once()


def test_cli_health_logs_missing_file(tmp_path, monkeypatch):
    """Test that health-logs exits with code 1 when log file does not exist."""
    non_existent = tmp_path / "dockfleet-health.log"
    monkeypatch.setattr("dockfleet.cli.main.HEALTH_LOG_PATH", non_existent)
    result = runner.invoke(app, ["health-logs", "--no-follow"])
    assert result.exit_code == 1
    assert "No health log file found yet." in result.stdout


def test_cli_health_logs_no_follow(tmp_path, monkeypatch):
    """Test that health-logs prints last N lines without follow."""
    log_file = tmp_path / "dockfleet-health.log"
    log_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
    monkeypatch.setattr("dockfleet.cli.main.HEALTH_LOG_PATH", log_file)
    result = runner.invoke(app, ["health-logs", "--no-follow", "--lines", "2"])
    assert result.exit_code == 0
    assert "line 1" not in result.stdout
    assert "line 2" in result.stdout
    assert "line 3" in result.stdout


def test_cli_health_logs_follow_truncation_resets_offset(tmp_path, monkeypatch):
    """Test that health-logs --follow detects file truncation (new_size < last_size) and resets offset."""
    log_file = tmp_path / "dockfleet-health.log"
    log_file.write_text("initial line 1\ninitial line 2\n", encoding="utf-8")
    monkeypatch.setattr("dockfleet.cli.main.HEALTH_LOG_PATH", log_file)

    call_count = 0

    def mock_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Truncate and write shorter content (simulating rotation/truncation)
            log_file.write_text("truncated new line\n", encoding="utf-8")
        elif call_count == 2:
            # Append another line
            with log_file.open("a", encoding="utf-8") as f:
                f.write("appended line\n")
        else:
            raise KeyboardInterrupt()

    monkeypatch.setattr("dockfleet.cli.main.time.sleep", mock_sleep)

    result = runner.invoke(app, ["health-logs", "--follow"])
    assert result.exit_code == 0
    assert "initial line 1" in result.stdout
    assert "truncated new line" in result.stdout
    assert "appended line" in result.stdout
    assert "Stopped following health logs." in result.stdout





