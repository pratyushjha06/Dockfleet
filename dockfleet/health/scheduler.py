import logging
import threading
import time


class HealthScheduler:
#  Background scheduler that will periodically run health checks.
    def __init__(self, interval_seconds: int = 30) -> None:
        self.interval_seconds = interval_seconds
        self._stopped = True
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)

    def start(self) -> None:
        # Start the background polling thread if it's not already running.
        if self._thread is not None and self._thread.is_alive():
            return

        self._stopped = False

        self._thread = threading.Thread(
            target=self._poll,
            daemon=True,
            name="HealthSchedulerThread",
        )
        self._thread.start()
        self._logger.info("HealthScheduler: started background thread")

    def stop(self) -> None:
        # Signal the polling thread to stop and wait for it to finish.
        self._stopped = True

        if self._thread is not None and self._thread.is_alive():
            self._logger.info("HealthScheduler: stopping background thread")
            # Wait for the thread to finish its current loop
            self._thread.join(timeout=self.interval_seconds + 5)
            self._logger.info("HealthScheduler: thread stopped")

    def _poll(self) -> None:
        # Main loop that will later run health checks.
        self._logger.info("HealthScheduler: poll loop started")

        while not self._stopped:
            self._logger.info("HealthScheduler: polling services...")
            time.sleep(self.interval_seconds)

        self._logger.info("HealthScheduler: poll loop exiting")
