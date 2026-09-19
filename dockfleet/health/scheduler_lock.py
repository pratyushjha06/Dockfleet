"""
Cross-process lock for HealthScheduler.

Prevents duplicate HealthScheduler instances from running against the same
project when ``dockfleet up`` is invoked from both the CLI and the dashboard
API concurrently.

Uses an OS-level file lock (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on
Windows) plus a PID tracking file so that stale locks left by crashed
processes are automatically recovered.

The lock is scoped to a project directory (typically where ``dockfleet.db``
lives), so two independent DockFleet projects can each run their own
scheduler without interfering with each other.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Platform-specific file locking ------------------------------------------------

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fd) -> None:
        """Acquire an exclusive, non-blocking lock on *fd*."""
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("lock held")

    def _unlock_file(fd) -> None:
        """Release a lock previously acquired with :func:`_lock_file`."""
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _lock_file(fd) -> None:
        """Acquire an exclusive, non-blocking lock on *fd*."""
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            raise RuntimeError("lock held")

    def _unlock_file(fd) -> None:
        """Release a lock previously acquired with :func:`_lock_file`."""
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass


# SchedulerLock ----------------------------------------------------------------


class SchedulerLock:
    """
    Project-scoped, cross-process lock for the health scheduler.

    Two files are created in *project_dir*:

    * ``.scheduler.lock`` – the OS-level lock file.  The first process to
      ``acquire()`` holds this file's lock until it calls ``release()`` or
      the process exits.
    * ``.scheduler.pid`` – a JSON file containing the holder's PID, a
      monotonic start-time, a random token, and the hostname.  This allows
      detection of stale locks (crashed process).  The token aids
      diagnostics but does not guard against PID reuse; if the OS reassigns
      a PID to an unrelated process while the lock file persists, manual
      cleanup is required.

    Parameters
    ----------
    project_dir:
        Directory used to scope the lock.  Typically the same directory that
        contains ``dockfleet.db``.
    """

    LOCK_FILENAME = ".scheduler.lock"
    PID_FILENAME = ".scheduler.pid"

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = Path(project_dir)
        self._lock_path = self._project_dir / self.LOCK_FILENAME
        self._pid_path = self._project_dir / self.PID_FILENAME
        self._fd: Optional[object] = None
        self._acquired = False
        self._token: str = uuid.uuid4().hex

    # -- public API -----------------------------------------------------------

    def acquire(self) -> None:
        """
        Try to acquire the scheduler lock.

        Raises
        ------
        RuntimeError
            If another live process already holds the lock.  The exception
            message includes the PID and host of the current holder so the
            user can diagnose the conflict.
        OSError
            If the lock files cannot be created (e.g. permissions).
        """
        self._project_dir.mkdir(parents=True, exist_ok=True)

        # Try to open (create) the lock file.
        try:
            self._fd = open(self._lock_path, "a+")
        except FileNotFoundError:
            # The parent directory may have been removed between mkdir and
            # open.  Retry once after re-creating it.
            self._project_dir.mkdir(parents=True, exist_ok=True)
            self._fd = open(self._lock_path, "a+")

        # Attempt the non-blocking OS lock.
        try:
            _lock_file(self._fd)
        except RuntimeError:
            # Another process holds the lock – figure out who.
            self._fd.close()
            self._fd = None
            self._raise_conflict()
            return  # not reached, but keeps type checkers happy

        # Lock acquired – write the PID tracking file.
        self._acquired = True
        self._write_pid_file()

    def release(self) -> None:
        """
        Release the scheduler lock and remove tracking files.

        Safe to call multiple times or when the lock is not held.
        Only removes files that belong to this instance.
        """
        if not self._acquired:
            return

        if self._fd is not None:
            try:
                _unlock_file(self._fd)
            except Exception:
                pass
            try:
                self._fd.close()
            except Exception:
                pass
            self._fd = None

        self._acquired = False

        for path in (self._pid_path, self._lock_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    @property
    def is_held(self) -> bool:
        """Return *True* if this instance currently holds the lock."""
        return self._acquired

    # -- internals ------------------------------------------------------------

    def _write_pid_file(self) -> None:
        """Persist current process info so other processes can diagnose conflicts."""
        info = {
            "pid": os.getpid(),
            "start_time": time.monotonic(),
            "token": self._token,
            "hostname": socket.gethostname(),
        }
        with open(self._pid_path, "w") as f:
            json.dump(info, f)

    def _read_pid_file(self) -> Optional[dict]:
        """Read the PID tracking file, returning *None* on any error."""
        try:
            with open(self._pid_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
            return None

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        """Return *True* if a process with *pid* is still alive.

        *PermissionError* is treated as alive: we cannot verify liveness
        but should not assume the holder is dead (which would let us
        steal its lock).
        """
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_process = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if h_process:
                kernel32.CloseHandle(h_process)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except PermissionError:
                # Cannot check — assume alive to avoid stealing the lock.
                return True
            except OSError:
                return False

    def _raise_conflict(self) -> None:
        """
        Raise :class:`RuntimeError` with a human-readable conflict message.

        Reads the existing ``.scheduler.pid`` to identify the holder.  If the
        holder's process is no longer running the lock is considered stale
        and is automatically recovered instead of raising.
        """
        info = self._read_pid_file()
        if info is not None:
            holder_pid = info.get("pid", -1)
            holder_host = info.get("hostname", "unknown")

            if self._pid_is_running(holder_pid):
                raise RuntimeError(
                    f"Health scheduler is already running for this project "
                    f"(PID {holder_pid} on {holder_host}). "
                    f"Stop the existing scheduler first, or if it crashed, "
                    f"delete {self._lock_path} and retry."
                )

            # PID is dead — recover the stale lock.
            logger.warning(
                "HealthScheduler: recovering stale lock (PID %d on %s "
                "is no longer running)",
                holder_pid,
                holder_host,
            )
            self._cleanup_stale()

            # Clean up done — try to acquire.
            self._fd = open(self._lock_path, "a+")
            try:
                _lock_file(self._fd)
            except RuntimeError:
                self._fd.close()
                self._fd = None
                raise RuntimeError(
                    "Could not acquire scheduler lock even after " "stale-lock recovery"
                )
            self._acquired = True
            self._write_pid_file()
            return

        # No readable PID file but lock is held – generic message.
        raise RuntimeError(
            f"Health scheduler lock is held by another process, but the "
            f"holder could not be identified. "
            f"Delete {self._lock_path} and retry."
        )

    def _cleanup_stale(self) -> None:
        """Remove stale lock and PID files left by a dead process."""
        for path in (self._pid_path, self._lock_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
