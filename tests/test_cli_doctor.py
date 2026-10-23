"""Tests for dockfleet doctor's client and daemon checks."""

import subprocess
from unittest.mock import patch

from typer.testing import CliRunner

from dockfleet.cli.main import app

runner = CliRunner()

CLIENT_OUTPUT = "Docker version 29.8.2, build abc\n"


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_doctor_reports_client_and_running_daemon():
    client = _completed(stdout=CLIENT_OUTPUT)
    info = _completed(stdout="29.8.2\n")

    with patch("dockfleet.cli.main.subprocess.run", side_effect=[client, info]) as run:
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Docker client: Docker version 29.8.2" in result.stdout
    assert "Docker daemon: running (server 29.8.2)" in result.stdout
    assert "Environment looks good" in result.stdout
    assert run.call_args_list[1].args[0] == [
        "docker",
        "info",
        "--format",
        "{{.ServerVersion}}",
    ]


def test_doctor_fails_when_daemon_is_unreachable():
    client = _completed(stdout=CLIENT_OUTPUT)
    info = _completed(
        returncode=1,
        stderr="Cannot connect to the Docker daemon at npipe:////./pipe/docker_engine\n",
    )

    with patch("dockfleet.cli.main.subprocess.run", side_effect=[client, info]):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Docker client: Docker version 29.8.2" in result.stdout
    assert "Docker daemon: unreachable" in result.stdout
    assert "Cannot connect to the Docker daemon" in result.stdout
    assert "Environment looks good" not in result.stdout


def test_doctor_fails_when_client_is_missing():
    with patch("dockfleet.cli.main.subprocess.run", side_effect=FileNotFoundError()):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Docker CLI: not found" in result.stdout


def test_doctor_fails_when_daemon_does_not_respond():
    client = _completed(stdout=CLIENT_OUTPUT)
    timeout = subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=5)

    with patch("dockfleet.cli.main.subprocess.run", side_effect=[client, timeout]):
        result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Docker daemon: unreachable (no response within 5s)" in result.stdout
