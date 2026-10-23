"""Tests for the shared scheduler lock scope.

The CLI used to scope the lock to the config file's parent directory while the
dashboard used the repository root, so both schedulers could start against the
same database. Every entry point now resolves the directory that holds the
health database, with DOCKFLEET_DB_URL honoured like the engine does.
"""

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dockfleet.cli.main import app, stop_background_scheduler
from dockfleet.health.models import DB_PATH
from dockfleet.health.scheduler_lock import SchedulerLock, default_lock_dir

runner = CliRunner()


def _write_config(path, with_healthcheck: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    healthcheck = ""
    if with_healthcheck:
        healthcheck = (
            "    healthcheck:\n"
            "      type: http\n"
            "      endpoint: http://localhost:8000/health\n"
            "      interval: 5\n"
        )
    path.write_text(
        "services:\n"
        "  api:\n"
        "    image: nginx:alpine\n"
        "    restart: always\n" + healthcheck
    )


def test_default_lock_dir_uses_the_database_directory(tmp_path):
    # conftest points DOCKFLEET_DB_URL at <tmp_path>/test_dockfleet.db
    assert default_lock_dir() == tmp_path


def test_default_lock_dir_follows_db_url_override(tmp_path, monkeypatch):
    other = tmp_path / "other"
    monkeypatch.setenv("DOCKFLEET_DB_URL", f"sqlite:///{other / 'health.db'}")

    assert default_lock_dir() == other.resolve()


def test_default_lock_dir_falls_back_for_memory_and_invalid_urls(monkeypatch):
    monkeypatch.setenv("DOCKFLEET_DB_URL", "sqlite:///:memory:")
    assert default_lock_dir() == DB_PATH.parent

    monkeypatch.setenv("DOCKFLEET_DB_URL", "not a valid url")
    assert default_lock_dir() == DB_PATH.parent


def test_cli_and_dashboard_resolve_the_same_lock_files():
    cli_lock = SchedulerLock(default_lock_dir())
    dashboard_lock = SchedulerLock(default_lock_dir())

    assert cli_lock._project_dir == dashboard_lock._project_dir
    assert cli_lock._lock_path == dashboard_lock._lock_path
    assert cli_lock._pid_path == dashboard_lock._pid_path


def test_stop_background_scheduler_defaults_to_database_scope(tmp_path):
    pid_file = tmp_path / SchedulerLock.PID_FILENAME
    lock_file = tmp_path / SchedulerLock.LOCK_FILENAME
    pid_file.write_text(json.dumps({"pid": 9999999}))
    lock_file.write_text("lock")

    assert stop_background_scheduler() is False
    assert not pid_file.exists()
    assert not lock_file.exists()


def test_dashboard_uses_the_database_lock_scope(monkeypatch):
    from dockfleet.dashboard import api

    created = {}

    class FakeScheduler:
        def __init__(self, config, project_dir=None):
            created["project_dir"] = project_dir

        def start(self):
            created["started"] = True

    monkeypatch.setattr(api, "HealthScheduler", FakeScheduler)
    monkeypatch.setattr(api, "init_db", lambda: None)
    monkeypatch.setattr(api, "bootstrap_from_path", lambda path: None)
    monkeypatch.setattr(api, "load_config", lambda path: SimpleNamespace(services={}))
    monkeypatch.setattr(
        api, "get_orchestrator", lambda **kwargs: SimpleNamespace(up=lambda: None)
    )
    monkeypatch.setattr(api, "ingest_docker_logs_once", lambda tail=200: None)
    monkeypatch.setattr(api, "_health_scheduler", None)

    api.on_startup()

    assert created["project_dir"] == default_lock_dir()
    assert created["started"] is True


@pytest.mark.parametrize("command", ["self-heal", "health-dev"])
def test_cli_loops_use_the_database_lock_scope(monkeypatch, tmp_path, command):
    import dockfleet.cli.main as cli

    config_file = tmp_path / "nested" / "dockfleet.yaml"
    _write_config(config_file, with_healthcheck=command == "health-dev")
    created = {}

    class FakeScheduler:
        def __init__(self, config, project_dir=None):
            created["project_dir"] = project_dir

        def start(self):
            created["started"] = True

        def stop(self):
            created["stopped"] = True

    def stop_loop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "HealthScheduler", FakeScheduler)
    monkeypatch.setattr(cli, "bootstrap_from_path", lambda path: None)
    monkeypatch.setattr(cli, "setup_health_logging", lambda: None)
    monkeypatch.setattr(cli.time, "sleep", stop_loop)

    result = runner.invoke(app, [command, str(config_file)])

    assert result.exit_code == 0
    assert created["project_dir"] == default_lock_dir()
    assert created["started"] is True
    assert created["stopped"] is True


def test_up_detached_finds_a_scheduler_in_the_database_scope(monkeypatch, tmp_path):
    import dockfleet.cli.main as cli

    config_file = tmp_path / "nested" / "dockfleet.yaml"
    _write_config(config_file)
    pid_file = tmp_path / SchedulerLock.PID_FILENAME
    pid_file.write_text(json.dumps({"pid": 4321}))

    spawned = []
    monkeypatch.setattr(cli, "spawn_background_scheduler", spawned.append)
    monkeypatch.setattr(cli, "bootstrap_from_path", lambda path: None)
    monkeypatch.setattr(
        SchedulerLock, "_pid_is_running", staticmethod(lambda pid: True)
    )
    monkeypatch.setattr(
        "dockfleet.core.orchestrator.Orchestrator.up", lambda self: None
    )

    result = runner.invoke(app, ["up", str(config_file), "--detach"])

    assert result.exit_code == 0, result.output
    assert "already running in background (PID 4321)" in result.stdout
    assert spawned == []
