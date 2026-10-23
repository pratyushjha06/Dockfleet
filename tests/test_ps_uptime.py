"""Regression tests for the uptime value shown by ``ps`` and the dashboard.

Uptime used to be printed as the HH:MM:SS part of ``StartedAt``, so any
container that had been running for more than a day reported a time of day
instead of an elapsed duration.
"""

import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from dockfleet.cli.config import DockFleetConfig, RestartPolicy, ServiceConfig
from dockfleet.core.orchestrator import Orchestrator


def _uptime_for(started_at: str, returncode: int = 0) -> str:
    config = DockFleetConfig(
        services={"api": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )
    orch = Orchestrator(config)
    result = subprocess.CompletedProcess(
        args=["docker", "inspect", "dockfleet_api"],
        returncode=returncode,
        stdout=f"{started_at}\n",
        stderr="",
    )

    with patch("dockfleet.core.orchestrator.subprocess.run", return_value=result):
        return orch._get_container_uptime("dockfleet_api")


def _started_ago(**kwargs) -> str:
    moment = datetime.now(timezone.utc) - timedelta(**kwargs)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def test_uptime_is_a_duration_for_containers_running_days():
    assert _uptime_for(_started_ago(days=4, hours=2, minutes=15)) == "Up 4d 02:15"


def test_uptime_is_a_duration_for_containers_running_hours():
    assert _uptime_for(_started_ago(hours=5, minutes=12)) == "Up 5h 12m"


def test_uptime_is_a_duration_for_containers_running_minutes():
    assert _uptime_for(_started_ago(minutes=42, seconds=7)) == "Up 42m 07s"


def test_uptime_is_a_duration_for_fresh_containers():
    value = _uptime_for(_started_ago(seconds=42))

    assert value.startswith("Up ")
    seconds = int(value.split()[1].rstrip("s"))
    assert 41 <= seconds <= 43


def test_uptime_parses_nanosecond_precision_timestamps():
    value = _uptime_for("2026-10-15T14:23:07.123456789Z")

    assert value.startswith("Up ")
    assert value != "Unknown"


def test_uptime_is_unknown_when_inspect_fails():
    assert _uptime_for("", returncode=1) == "Unknown"


def test_uptime_is_unknown_for_unparsable_timestamps():
    assert _uptime_for("not-a-timestamp") == "Unknown"
