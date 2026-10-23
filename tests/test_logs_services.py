"""Regression tests for the dashboard log helper.

``docker logs`` writes non-TTY container stderr to the CLI's stderr stream,
so the helper has to merge both streams.  A nonzero exit code must surface as
an error instead of an empty result.
"""

import asyncio
import subprocess
from unittest.mock import patch

import httpx
from httpx import ASGITransport

from dockfleet.core.logs import get_logs_services
from dockfleet.dashboard.api import app


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=["docker", "logs"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_stderr_only_output_is_returned():
    result = _completed(stderr="application error\n")

    with patch("dockfleet.core.logs.subprocess.run", return_value=result):
        assert get_logs_services("api") == ["application error"]


def test_stdout_and_stderr_are_merged():
    result = _completed(stdout="started\n", stderr="warning: cache miss\n")

    with patch("dockfleet.core.logs.subprocess.run", return_value=result):
        assert get_logs_services("api") == ["started", "warning: cache miss"]


def test_failed_command_reports_stderr():
    result = _completed(
        returncode=1,
        stderr="Error response from daemon: No such container: dockfleet_api\n",
    )

    with patch("dockfleet.core.logs.subprocess.run", return_value=result):
        logs = get_logs_services("api")

    assert logs == [
        "Error fetching logs: Error response from daemon: "
        "No such container: dockfleet_api"
    ]


def test_failed_command_without_stderr_reports_exit_code():
    result = _completed(returncode=2)

    with patch("dockfleet.core.logs.subprocess.run", return_value=result):
        logs = get_logs_services("api")

    assert logs == ["Error fetching logs: docker logs exited with code 2"]


def test_no_output_returns_an_empty_list():
    with patch("dockfleet.core.logs.subprocess.run", return_value=_completed()):
        assert get_logs_services("api") == []


def test_subprocess_failure_is_reported():
    with patch(
        "dockfleet.core.logs.subprocess.run", side_effect=FileNotFoundError("docker")
    ):
        logs = get_logs_services("api")

    assert len(logs) == 1
    assert logs[0].startswith("Error fetching logs:")


def test_logs_endpoint_returns_stderr_lines():
    result = _completed(stderr="db connection lost\n")

    async def _run():
        with patch("dockfleet.core.logs.subprocess.run", return_value=result):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get("/logs", params={"service_name": "api"})

    response = asyncio.run(_run())

    assert response.status_code == 200
    assert response.json() == ["db connection lost"]
