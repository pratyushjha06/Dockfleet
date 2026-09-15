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
