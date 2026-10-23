"""Tests for dashboard configuration discovery.

The dashboard used to look up ``examples/dockfleet.yaml`` relative to the
process working directory, so starting uvicorn from anywhere else left the
dashboard with no services.  The config anchor is now the project root, with
an optional environment override.
"""

import pytest

from dockfleet.dashboard.api import _get_default_config_path, on_startup
from dockfleet.health.models import PROJECT_ROOT


def test_default_config_path_is_anchored_to_project_root(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKFLEET_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    path = _get_default_config_path()

    assert path == PROJECT_ROOT / "examples" / "dockfleet.yaml"
    assert path.is_absolute()
    assert path.exists()


def test_environment_override_wins(tmp_path, monkeypatch):
    custom = tmp_path / "custom.yaml"
    custom.write_text("services: {}\n")
    monkeypatch.setenv("DOCKFLEET_CONFIG", str(custom))

    assert _get_default_config_path() == custom.resolve()


def test_relative_override_resolves_against_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOCKFLEET_CONFIG", "custom.yaml")

    assert _get_default_config_path() == (tmp_path / "custom.yaml").resolve()


def test_missing_config_file_stops_startup(tmp_path, monkeypatch):
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("DOCKFLEET_CONFIG", str(missing))

    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        on_startup()
