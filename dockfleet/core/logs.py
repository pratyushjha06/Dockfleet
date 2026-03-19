import subprocess
import logging
import asyncio
from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from dockfleet.health.status import engine
from dockfleet.core.orchestrator import get_container_name
from dockfleet.health.logs import LogEntry
logger = logging.getLogger(__name__)


async def stream_container_logs(service_name: str):
    """Stream Docker logs → SSE with resilience."""
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
            yield f"data: {{ \"error\": \"Container {container} not found\" }}\n\n"
            return
    except Exception:
        yield f"data: {{ \"error\": \"Failed to check container {container}\" }}\n\n"
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
                yield f"data: {line}\n\n"
            # optional: small sleep to be nicer in async loop
            await asyncio.sleep(0)
    except GeneratorExit:
        logger.info("Client disconnected from %s logs", container)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info("Logs stream for %s cleaned up")


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
        logger.error("Failed to stream logs (sync) for %s: %s", container, e)
        return []
    
def get_logs_services(service_name: str, limit: int = 100):
    """Fetch last N logs (non-streaming)"""

    container = get_container_name(service_name)

    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(limit), container],
            capture_output=True,
            text=True
        )

        logs = result.stdout.strip().split("\n")

        return logs

    except Exception as e:
        return [f"Error fetching logs: {str(e)}"]
    
MAX_LOGS_PER_SERVICE = 1000

def store_log_line(service_name: str, message: str) -> None:
    try:
        with Session(engine) as session:

            log = LogEntry(
                service_name=service_name,
                message=message
            )
            session.add(log)

            old_logs = session.exec(
                select(LogEntry)
                .where(LogEntry.service_name == service_name)
                .order_by(LogEntry.timestamp.desc())
                .offset(MAX_LOGS_PER_SERVICE)
            ).all()

            for old in old_logs:
                session.delete(old)

            session.commit()

    except Exception:
        pass
