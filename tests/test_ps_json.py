import json
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

from dockfleet.cli.main import app

runner = CliRunner()


@pytest.fixture
def mock_dockfleet_yaml(tmp_path):
    yaml_file = tmp_path / "dockfleet.yaml"
    yaml_file.write_text(
        """
version: "1.0"
project: test
services:
  api:
    image: python:3.10
    restart: always
  redis:
    image: redis:alpine
    restart: always
"""
    )
    return str(yaml_file)


def test_normal_dockfleet_ps(mock_dockfleet_yaml):
    """Test normal human-readable dockfleet ps output."""
    with patch("dockfleet.core.docker.DockerManager.list_containers") as mock_list:
        result = runner.invoke(app, ["ps", mock_dockfleet_yaml])
        assert result.exit_code == 0
        assert "Listing running containers..." in result.stdout
        mock_list.assert_called_once()


def test_dockfleet_ps_json(mock_dockfleet_yaml):
    """Test dockfleet ps --json produces valid JSON output with service details."""
    mock_containers = [
        {
            "Names": "dockfleet_api",
            "Status": "Up 5 minutes (healthy)",
            "State": "running",
        },
        {
            "Names": "dockfleet_redis",
            "Status": "Up 2 minutes",
            "State": "running",
        },
    ]

    with patch(
        "dockfleet.core.docker.DockerManager.get_containers_json",
        return_value=mock_containers,
    ):
        result = runner.invoke(app, ["ps", "--json", mock_dockfleet_yaml])
        assert result.exit_code == 0
        # Ensure human-readable header is not in stdout
        assert "Listing running containers..." not in result.stdout

        # Verify JSON validity
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

        assert parsed[0] == {
            "name": "api",
            "status": "running",
            "health": "healthy",
        }
        assert parsed[1] == {
            "name": "redis",
            "status": "running",
            "health": "healthy",
        }


def test_dockfleet_ps_json_empty(mock_dockfleet_yaml):
    """Test dockfleet ps --json when no services/containers are running."""
    with patch(
        "dockfleet.core.docker.DockerManager.get_containers_json",
        return_value=[],
    ):
        result = runner.invoke(app, ["ps", "--json", mock_dockfleet_yaml])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == []


def test_dockfleet_ps_json_error_handling():
    """Test error handling when dockfleet ps --json encounters an error."""
    # Passing a non-existent configuration file
    result = runner.invoke(app, ["ps", "--json", "non_existent_config.yaml"])
    assert result.exit_code == 1
    # Check that error message is present in output and not invalid json
    assert "not found" in result.output
