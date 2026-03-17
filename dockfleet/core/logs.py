from fastapi.responses import StreamingResponse
from fastapi import HTTPException
import subprocess
import logging
from dockfleet.core.orchestrator import get_container_name
from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
from sqlmodel import Session, select
from dockfleet.health.models import engine

logger = logging.getLogger(__name__)

async def stream_container_logs(service_name: str):
    """Stream Docker logs → SSE with resilience."""
    container = get_container_name(service_name)
    
    # Check container exists first
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={container}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5
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
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
        text=True, bufsize=1, universal_newlines=True
    )
    
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                yield f"data: {line}\n\n"
    except GeneratorExit:
        logger.info(f"Client disconnected from {container} logs")
    finally:
        # Cleanup Popen on disconnect/timeout
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info(f"Logs stream for {container} cleaned up")

class LogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    service_name: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

def store_log_line(service_name: str, message: str) -> None:
    MAX_LOGS_PER_SERVICE = 1000
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