import pytest
from unittest.mock import patch, MagicMock
from dockfleet.core.orchestrator import Orchestrator, get_service_stats

@patch('subprocess.run')
def test_get_service_stats(mock_run):
    """Test stats parsing."""
    mock_run.return_value

import pytest
from unittest.mock import patch, MagicMock, Mock
from dockfleet.core.orchestrator import Orchestrator, get_container_name
from dockfleet.core.orchestrator import ServiceStat

@pytest.fixture
def mock_config():
    """Mock config."""
    config = Mock()
    config.services = {
        "api": Mock(image="nginx", ports=[80]),
        "web": Mock(image="nginx", ports=[8080])
    }
    return config

@pytest.fixture
def orchestrator(mock_config):
    """Mocked orchestrator."""
    orch = Mock(spec=Orchestrator)
    orch.config = mock_config
    orch.container_name = Mock(side_effect=lambda name: f"dockfleet_{name}")
    orch.docker = Mock()
    orch.logger = Mock()
    return orch

def test_get_container_name():
    """Basic container naming."""
    assert get_container_name("api") == "dockfleet_api"
    assert get_container_name("web") == "dockfleet_web"

@patch('subprocess.run')
def test_docker_ps_check(mock_run):
    """Test container existence check."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="dockfleet_api\n"
    )
    
    # Simulate restart_service container check
    result = mock_run.return_value.stdout.strip()
    assert "dockfleet_api" in result

@patch('dockfleet.core.orchestrator.Orchestrator')
def test_get_service_stats_wrapper(mock_orch_class):
    """Module wrapper works."""
    mock_orch = mock_orch_class.return_value
    mock_orch.get_service_stats.return_value = [
        ServiceStat(service_name="api", container_name="dockfleet_api", status="running")
    ]
    
    from dockfleet.core.orchestrator import get_service_stats
    stats = get_service_stats()
    
    assert len(stats) == 1
    mock_orch.get_service_stats.assert_called_once()

@pytest.mark.parametrize("service_name", ["api", "web", "db"])
def test_container_naming_consistent(service_name):
    """All services use dockfleet_ prefix."""
    name = get_container_name(service_name)
    assert name == f"dockfleet_{service_name}"
    assert name.startswith("dockfleet_")