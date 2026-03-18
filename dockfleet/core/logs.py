import subprocess
import logging
import asyncio 

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
        logger.info("Logs stream for %s cleaned up", container)
