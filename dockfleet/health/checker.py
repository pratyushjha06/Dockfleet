import logging
import socket
import subprocess
from typing import Optional

import requests


class HealthChecker:
    def __init__(self) -> None:
        # Simple logger for health checks
        self._logger = logging.getLogger(__name__)

    def check_http(self, endpoint: str, timeout: float = 3.0) -> bool:
        """
        Return True if HTTP endpoint looks healthy, else False.
        Healthy = 2xx or 3xx status code.
        """
        try:
            resp = requests.get(endpoint, timeout=timeout)
            code = resp.status_code

            if 200 <= code < 400:
                self._logger.info("HTTP OK %s (status=%s)", endpoint, code)
                return True

            self._logger.warning(
                "HTTP UNHEALTHY %s (status=%s)", endpoint, code
            )
            return False

        except requests.RequestException as exc:
            self._logger.warning(
                "HTTP check FAILED %s (%s)", endpoint, exc
            )
            return False

    def check_tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
        # Return True if TCP connection to host:port succeeds, else False.
        try:
            sock: Optional[socket.socket] = None
            sock = socket.create_connection((host, port), timeout=timeout)
            self._logger.info("TCP OK %s:%s", host, port)
            return True
        except OSError as exc:
            self._logger.warning(
                "TCP check FAILED %s:%s (%s)", host, port, exc
            )
            return False
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def check_process(self, container_name: str) -> bool:
        """
        Return True if Docker container is running, else False.

        Uses:
            docker inspect -f "{{.State.Running}}" <container_name>
        """
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    container_name,
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            # docker CLI not installed / not on PATH
            self._logger.warning(
                "Process check FAILED for %s (docker CLI not found)",
                container_name,
            )
            return False

        if result.returncode != 0:
            # e.g. container does not exist
            self._logger.warning(
                "Process check FAILED for %s (docker inspect error: %s)",
                container_name,
                result.stderr.strip(),
            )
            return False

        output = result.stdout.strip().lower()
        if output == "true":
            self._logger.info("PROCESS OK %s (running)", container_name)
            return True

        self._logger.warning(
            "PROCESS UNHEALTHY %s (running=%s)", container_name, output
        )
        return False
