import subprocess
import logging
import asyncio
from typing import Optional

from dockfleet.core.orchestrator import get_container_name
from dockfleet.health.logs import store_log_line as store_log_line_in_db

logger = logging.getLogger(__name__)


async def stream_container_logs(service_name: str):
    """Stream Docker logs → SSE and sample lines into DB for history view."""
    container = f"dockfleet_{service_name}"

    # Check container exists first
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={container}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if not result.stdout.strip():
            yield f'data: {{ "error": "Container {container} not found" }}\n\n'
            return
    except Exception:
        yield f'data: {{ "error": "Failed to check container {container}" }}\n\n'
        return

    # Stream logs with tail=100, follow
    cmd = ["docker", "logs", "--tail", "100", "-f", container]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                # Send to frontend as SSE
                yield f"data: {line}\n\n"

                # Also store in DB so /logs/db has history
                try:
                    store_log_line_in_db(
                        service_name=service_name,
                        message=line,
                        source="docker-logs",
                    )
                except Exception:
                    logger.exception("Failed to store log line for %s", service_name)

            await asyncio.sleep(0)
    except GeneratorExit:
        logger.info("Client disconnected from %s logs", container)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info("Logs stream for %s cleaned up", container)


# backward compatibility for old sync callers/tests
def stream_logs(service_name: str):
    """Sync wrapper: returns an iterator of plain log lines (no SSE formatting)."""
    container = f"dockfleet_{service_name}"
    cmd = ["docker", "logs", "--tail", "100", container]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.error("Failed to get logs for %s", container)
            return []

        for line in result.stdout.splitlines():
            line = line.rstrip()
            if line:
                yield line
    except Exception as e:
        logger.error("Failed to stream logs (sync) for %s: %s", service_name, e)
        return []


def get_logs_services(service_name: str, limit: int = 100):
    """Fetch last N logs (non-streaming)."""
    container = get_container_name(service_name)

    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(limit), container],
            capture_output=True,
            text=True,
        )
        logs = result.stdout.strip().split("\n")
        return logs
    except Exception as e:
        return [f"Error fetching logs: {str(e)}"]


def store_log_line(service_name: str, message: str) -> None:
    """
    Backwards-compatible wrapper that stores a log line in the
    central LogEvent table via health.logs.store_log_line.
    """
    try:
        store_log_line_in_db(
            service_name=service_name,
            message=message,
            source="core.logs",
        )
    except Exception:
        # Swallow errors to avoid breaking log streaming callers
        logger.exception("Failed to store log line for %s", service_name)