import subprocess
from unittest.mock import patch

import pytest

from dockfleet.core.docker import DockerManager


class TestDockerManagerCreateNetwork:
    def test_create_network_succeeds(self):
        manager = DockerManager()

        with patch("dockfleet.core.docker.subprocess.run") as run:
            manager.create_network("dockfleet")

        run.assert_called_once_with(
            ["docker", "network", "create", "dockfleet"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_existing_network_is_idempotent(self):
        manager = DockerManager()
        error = subprocess.CalledProcessError(
            1,
            ["docker", "network", "create", "dockfleet"],
            stderr="Error response from daemon: network with name dockfleet already exists",
        )

        with patch("dockfleet.core.docker.subprocess.run", side_effect=error):
            manager.create_network("dockfleet")

    @pytest.mark.parametrize(
        "stderr",
        [
            "Cannot connect to the Docker daemon",
            "permission denied while trying to connect to the Docker daemon socket",
            "Error response from daemon: invalid network name",
            "unexpected docker failure",
        ],
    )
    def test_real_network_creation_failures_are_not_swallowed(self, stderr):
        manager = DockerManager()
        error = subprocess.CalledProcessError(
            1,
            ["docker", "network", "create", "dockfleet"],
            stderr=stderr,
        )

        with patch("dockfleet.core.docker.subprocess.run", side_effect=error):
            with pytest.raises(subprocess.CalledProcessError) as raised:
                manager.create_network("dockfleet")

        assert raised.value is error

    def test_missing_stderr_does_not_hide_failure(self):
        manager = DockerManager()
        error = subprocess.CalledProcessError(
            1,
            ["docker", "network", "create", "dockfleet"],
        )

        with patch("dockfleet.core.docker.subprocess.run", side_effect=error):
            with pytest.raises(subprocess.CalledProcessError):
                manager.create_network("dockfleet")


class TestDockerManagerStopContainer:
    """docker stop must be idempotent and must surface Docker's stderr on real failures."""

    @staticmethod
    def _completed(
        returncode: int = 0, stderr: str = ""
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["docker", "stop", "dockfleet_api"],
            returncode=returncode,
            stdout="",
            stderr=stderr,
        )

    def test_stop_container_success(self):
        manager = DockerManager()

        with patch(
            "dockfleet.core.docker.subprocess.run", return_value=self._completed()
        ) as run:
            manager.stop_container("dockfleet_api")

        run.assert_called_once_with(
            ["docker", "stop", "dockfleet_api"],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_absent_container_is_treated_as_already_stopped(self):
        manager = DockerManager()
        result = self._completed(
            returncode=1,
            stderr="Error response from daemon: No such container: dockfleet_api\n",
        )

        with patch("dockfleet.core.docker.subprocess.run", return_value=result):
            manager.stop_container("dockfleet_api")

    @patch("dockfleet.core.docker.subprocess.run")
    def test_stop_container_timeout_raises_runtime_error(self, mock_run):
        manager = DockerManager()
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["docker", "stop", "dockfleet_api"], timeout=60
        )

        with pytest.raises(RuntimeError, match="timed out after 60 seconds"):
            manager.stop_container("dockfleet_api")

    @patch("dockfleet.core.docker.subprocess.run")
    def test_daemon_failure_raises_runtime_error_with_stderr(self, mock_run):
        manager = DockerManager()
        mock_run.return_value = self._completed(
            returncode=1,
            stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock\n",
        )

        with pytest.raises(RuntimeError, match="Cannot connect to the Docker daemon"):
            manager.stop_container("dockfleet_api")

    @patch("dockfleet.core.docker.subprocess.run")
    def test_failure_without_stderr_falls_back_to_exit_code(self, mock_run):
        manager = DockerManager()
        mock_run.return_value = self._completed(returncode=2, stderr="")

        with pytest.raises(RuntimeError, match="exit code 2"):
            manager.stop_container("dockfleet_api")
