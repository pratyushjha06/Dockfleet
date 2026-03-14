import pytest
from unittest.mock import patch, MagicMock
from dockfleet.core.orchestrator import Orchestrator, get_service_stats

@patch('subprocess.run')
def test_get_service_stats(mock_run):
    """Test stats parsing."""
    mock_run.return_value
