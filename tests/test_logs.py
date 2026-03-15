import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from dockfleet.core.logs import stream_logs
from dockfleet.core.orchestrator import get_container_name

def test_get_container_name():
    """Helper works."""
    assert get_container_name("api") == "dockfleet_api"

@patch('dockfleet.core.logs.subprocess.run')
@pytest.mark.asyncio
async def test_container_missing(mock_run):
    """Container not found → SSE error."""
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    
    events = []
    async for event in stream_logs("missing"):
        events.append(event)
        break
    
    assert len(events) == 1
    assert '"Container dockfleet_missing not found"' in events[0]
    mock_run.assert_called_once()

@patch('dockfleet.core.logs.subprocess.run')
@pytest.mark.asyncio
async def test_docker_check_fails(mock_run):
    """Docker ps fails → error SSE."""
    mock_run.side_effect = Exception("Docker error")
    
    events = []
    async for event in stream_logs("api"):
        events.append(event)
        break
    
    assert len(events) == 1
    assert '"Failed to check container dockfleet_api"' in events[0]

@patch('dockfleet.core.logs.subprocess.Popen')
@patch('dockfleet.core.logs.subprocess.run')
@pytest.mark.asyncio
async def test_logs_streaming(mock_run, mock_popen):
    """Container exists → streams SSE."""
    # Container check passes
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="dockfleet_test\n"),  # docker ps
    ]
    
    # Mock log lines
    mock_popen.return_value.stdout = iter([
        "log line 1\n",
        "log line 2\n",
    ])

    mock_popen.return_value.terminate = MagicMock()
    mock_popen.return_value.wait = MagicMock(return_value=0)

    events = []
    async for event in stream_logs("test"):
        events.append(event)
        if len(events) >= 2:
            break
    
    assert len(events) >= 1
    assert all("data: " in event for event in events)
