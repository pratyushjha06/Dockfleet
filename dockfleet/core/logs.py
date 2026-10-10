import asyncio
import concurrent.futures
import logging
import subprocess
import threading

from dockfleet.core.orchestrator import get_container_name
from dockfleet.health.logs import store_log_line as store_log_line_in_db

logger = logging.getLogger(__name__)


async def stream_container_logs(service_name: str, request: any = None):
    """100% reliable: async log streaming with concurrent stdout/stderr draining."""
    container = f"dockfleet_{service_name}"
    loop = asyncio.get_running_loop()

    max_retries = 20
    for attempt in range(max_retries):
        proc = None
        t_stdout = None
        t_stderr = None
        stop_readers = threading.Event()
        try:
            cmd = ["docker", "logs", "--tail", "5", "-f", container]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
            queue = asyncio.Queue(maxsize=1000)
            stderr_buffer = []

            def enqueue_item(item):
                """Enqueue log line into asyncio queue thread-safely, dropping oldest entries when full."""
                if stop_readers.is_set() or loop.is_closed():
                    return

                def _push():
                    if stop_readers.is_set():
                        return
                    is_sentinel = item[1] is None or item[0] in ("stdout_error", "stderr_error")
                    if is_sentinel:
                        if queue.full():
                            try:
                                popped = queue.get_nowait()
                                if popped[1] is None or popped[0] in ("stdout_error", "stderr_error"):
                                    # Don't drop existing sentinel, put it back
                                    try:
                                        queue.put_nowait(popped)
                                    except Exception:
                                        pass
                            except (asyncio.QueueEmpty, Exception):
                                pass
                        try:
                            queue.put_nowait(item)
                        except asyncio.QueueFull:
                            try:
                                queue.get_nowait()
                                queue.put_nowait(item)
                            except Exception:
                                pass
                    else:
                        if queue.full():
                            try:
                                popped = queue.get_nowait()
                                if popped[1] is None or popped[0] in ("stdout_error", "stderr_error"):
                                    # Never drop a sentinel to make room for a log line
                                    try:
                                        queue.put_nowait(popped)
                                    except Exception:
                                        pass
                                    return
                            except (asyncio.QueueEmpty, Exception):
                                pass
                        try:
                            queue.put_nowait(item)
                        except (asyncio.QueueFull, Exception):
                            pass

                try:
                    loop.call_soon_threadsafe(_push)
                except RuntimeError:
                    # Event loop is closed
                    stop_readers.set()

            def read_stdout():
                """Drain stdout stream lines and queue them."""
                try:
                    if proc.stdout is not None:
                        while not stop_readers.is_set():
                            line = proc.stdout.readline()
                            if not line or not isinstance(line, str):
                                break
                            enqueue_item(("stdout", line))
                except (OSError, ValueError) as e:
                    if not stop_readers.is_set():
                        logger.exception(
                            "Stdout reader expected exception for %s", container
                        )
                        enqueue_item(("stdout_error", e))
                except Exception as e:
                    if not stop_readers.is_set():
                        logger.exception(
                            "Stdout reader unexpected exception for %s", container
                        )
                        enqueue_item(("stdout_error", e))
                finally:
                    enqueue_item(("stdout", None))

            def read_stderr():
                """Drain stderr stream lines and queue them."""
                try:
                    if proc.stderr is not None:
                        while not stop_readers.is_set():
                            line = proc.stderr.readline()
                            if not line or not isinstance(line, str):
                                break
                            enqueue_item(("stderr", line))
                except (OSError, ValueError) as e:
                    if not stop_readers.is_set():
                        logger.exception(
                            "Stderr reader expected exception for %s", container
                        )
                        enqueue_item(("stderr_error", e))
                except Exception as e:
                    if not stop_readers.is_set():
                        logger.exception(
                            "Stderr reader unexpected exception for %s", container
                        )
                        enqueue_item(("stderr_error", e))
                finally:
                    enqueue_item(("stderr", None))

            t_stdout = loop.run_in_executor(None, read_stdout)
            t_stderr = loop.run_in_executor(None, read_stderr)

            active_streams = 2
            while active_streams > 0:
                if request is not None and hasattr(request, "is_disconnected"):
                    try:
                        if await request.is_disconnected():
                            return
                    except Exception:
                        pass

                # If reader threads have finished and queue is drained, terminate promptly
                if t_stdout.done() and t_stderr.done() and queue.empty():
                    break

                try:
                    stream_type, payload = await asyncio.wait_for(
                        queue.get(), timeout=0.2
                    )
                except asyncio.TimeoutError:
                    if t_stdout.done() and t_stderr.done() and queue.empty():
                        break
                    if request is not None and hasattr(request, "is_disconnected"):
                        try:
                            if await request.is_disconnected():
                                return
                        except Exception:
                            pass
                    continue
                if stream_type in ("stdout_error", "stderr_error"):
                    logger.error(
                        "%s encountered error for container %s: %s",
                        stream_type,
                        container,
                        payload,
                    )
                    yield f"data: [dockfleet] Error reading logs for '{container}': {payload}\n\n"
                    return

                if payload is None:
                    active_streams -= 1
                    continue

                cleaned_line = payload.rstrip()
                if cleaned_line:
                    try:
                        store_log_line_in_db(
                            service_name=service_name,
                            message=cleaned_line,
                            source=f"docker-logs-{stream_type}",
                        )
                    except Exception:
                        logger.exception(
                            "Failed to persist %s log line for %s",
                            stream_type,
                            service_name,
                        )

                    if stream_type == "stderr":
                        stderr_buffer.append(cleaned_line)
                        if len(stderr_buffer) > 100:
                            stderr_buffer.pop(0)

                    yield f"data: {cleaned_line}\n\n"

            await asyncio.gather(t_stdout, t_stderr, return_exceptions=True)

            # Check process exit code after streams EOF
            def wait_proc():
                """Wait for process completion with timeout fallback."""
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

            await loop.run_in_executor(None, wait_proc)

            if proc.returncode != 0:
                stderr_output = "\n".join(stderr_buffer).strip()
                if not stderr_output and proc.stderr:
                    try:
                        stderr_output = proc.stderr.read().strip()
                    except Exception:
                        pass

                stderr_lower = stderr_output.lower()

                if (
                    "no such container" in stderr_lower
                    or "no such image" in stderr_lower
                ):
                    logger.error(
                        "Container %s does not exist: %s", container, stderr_output
                    )
                    yield f"data: [dockfleet] Container '{container}' not found\n\n"
                    return
                elif (
                    "permission denied" in stderr_lower
                    or "access is denied" in stderr_lower
                ):
                    logger.error(
                        "Permission denied accessing logs for %s: %s",
                        container,
                        stderr_output,
                    )
                    yield f"data: [dockfleet] Permission denied accessing logs for '{container}'\n\n"
                    return
                elif (
                    "connection refused" in stderr_lower
                    or "cannot connect" in stderr_lower
                    or "is the docker daemon running" in stderr_lower
                ):
                    logger.warning(
                        "Docker daemon unreachable for %s (attempt %d/%d): %s",
                        container,
                        attempt + 1,
                        max_retries,
                        stderr_output,
                    )
                else:
                    logger.error(
                        "Docker logs exited with code %d for %s: %s",
                        proc.returncode,
                        container,
                        stderr_output,
                    )
                    yield f"data: [dockfleet] Error streaming logs: {stderr_output or f'exited with code {proc.returncode}'}\n\n"
                    return
            else:
                return

        except FileNotFoundError:
            logger.error(
                "Docker binary not found. Cannot stream logs for %s", container
            )
            yield "data: [dockfleet] Docker is not installed or not in PATH\n\n"
            return
        except PermissionError as e:
            logger.error(
                "Permission denied accessing logs for %s: %s", container, e
            )
            yield f"data: [dockfleet] Permission denied accessing logs for '{container}'\n\n"
            return
        except Exception as e:
            error_msg = str(e).lower()

            if "no such container" in error_msg or "no such image" in error_msg:
                logger.error("Container %s not found: %s", container, e)
                yield f"data: [dockfleet] Container '{container}' not found\n\n"
                return
            elif (
                "permission denied" in error_msg or "access is denied" in error_msg
            ):
                logger.error(
                    "Permission denied accessing logs for %s: %s", container, e
                )
                yield f"data: [dockfleet] Permission denied accessing logs for '{container}'\n\n"
                return
            elif "connection refused" in error_msg or "cannot connect" in error_msg:
                logger.warning(
                    "Docker daemon unreachable for %s (attempt %d/%d): %s",
                    container,
                    attempt + 1,
                    max_retries,
                    e,
                )
            else:
                logger.exception(
                    "Unexpected error streaming logs for %s (attempt %d/%d)",
                    container,
                    attempt + 1,
                    max_retries,
                )
                yield f"data: [dockfleet] Error streaming logs: {e}\n\n"
                return
        finally:
            stop_readers.set()
            if proc is not None:

                def cleanup():
                    """Terminate, close pipes, and kill subprocess safely."""
                    try:
                        if proc.stdout and hasattr(proc.stdout, "close"):
                            try:
                                proc.stdout.close()
                            except Exception:
                                pass
                        if proc.stderr and hasattr(proc.stderr, "close"):
                            try:
                                proc.stderr.close()
                            except Exception:
                                pass
                        if proc.poll() is None:
                            proc.terminate()
                            proc.wait(timeout=1)
                    except Exception:
                        try:
                            if proc.poll() is None:
                                proc.kill()
                                proc.wait()
                        except Exception:
                            logger.warning(
                                "Failed to kill process for container %s on attempt %d",
                                container,
                                attempt + 1,
                                exc_info=True,
                            )

                try:
                    await asyncio.shield(loop.run_in_executor(None, cleanup))
                except Exception:
                    cleanup()

            if t_stdout is not None or t_stderr is not None:
                readers = [t for t in (t_stdout, t_stderr) if t is not None]
                try:
                    await asyncio.shield(
                        asyncio.gather(*readers, return_exceptions=True)
                    )
                except Exception:
                    pass

        if attempt < max_retries - 1:
            await asyncio.sleep(1)

    yield "data: [dockfleet] Max retries exhausted - container may still be starting\n\n"


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
        logger.exception("Failed to store log line for %s", service_name)
