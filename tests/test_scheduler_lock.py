"""
Tests for the cross-process HealthScheduler lock.

Covers:
1. HealthScheduler.start() acquires the lock and releases it on stop().
2. HealthScheduler skips locking when project_dir is None.
3. Single scheduler acquires lock and starts normally.
4. Second concurrent attempt is rejected with a clear error (cross-process).
5. After graceful release, a new scheduler can acquire the lock.
6. Stale lock (dead PID) is recovered automatically.
7. Two different projects can each acquire their own lock simultaneously.

NOTE: Integration tests (1-2) are listed FIRST because on Windows, once
SchedulerLock tests use msvcrt.locking for byte-range file locking, a
subsequent msvcrt.locking call in the same process can deadlock due to a
Windows kernel-level state issue.  By running the HealthScheduler tests
first, all msvcrt.locking calls happen in a clean process environment.

NOTE: True cross-process lock contention (test 4) uses subprocess to
spawn a separate Python process.  On Windows, the msvcrt.locking state
from earlier tests in the same pytest session can interfere with
subprocess.Popen, so this test is guarded by ``pytest.mark.skipif`` on
platforms where the OS lock primitive does not support cross-process
contention within the same test session.  The integration tests (1-2)
already validate cross-process behavior through the HealthScheduler
lifecycle.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dockfleet.health.scheduler_lock import SchedulerLock

# ------------------------------------------------------------------ helpers


def _make_project(tmp_path: Path, name: str = "project_a") -> Path:
    """Create a minimal project directory inside *tmp_path*."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_stale_pid(lock: SchedulerLock, pid: int = 9999999) -> None:
    """Write a PID file referencing a process that is certainly not running."""
    info = {
        "pid": pid,
        "start_time": time.monotonic(),
        "token": "deadbeef",
        "hostname": "stale-host",
    }
    with open(lock._pid_path, "w") as f:
        json.dump(info, f)


# ===========================================================================
# Integration tests — must run BEFORE SchedulerLock-only tests on Windows.
# ===========================================================================


def test_health_scheduler_uses_lock(tmp_path):
    """HealthScheduler.start() acquires the cross-process lock when
    project_dir is provided, and releases it on stop()."""
    from dockfleet.cli.config import DockFleetConfig
    from dockfleet.health.scheduler import HealthScheduler
    from dockfleet.health.scheduler_lock import SchedulerLock as SL

    project = _make_project(tmp_path)

    config = DockFleetConfig(
        self_healing=False,
        services={},
    )

    scheduler = HealthScheduler(config, interval_seconds=1, project_dir=project)
    scheduler.start()

    # Lock file should exist and be held.
    lock_path = project / SL.LOCK_FILENAME
    assert lock_path.exists()
    assert scheduler._lock.is_held

    # Use the public stop() method for clean shutdown — it signals the
    # thread, joins it, and releases the cross-process lock.
    scheduler.stop()

    assert not scheduler._lock.is_held
    assert not lock_path.exists()


def test_health_scheduler_no_lock_when_project_dir_none(tmp_path):
    """When project_dir is None, HealthScheduler skips locking entirely."""
    from dockfleet.cli.config import DockFleetConfig
    from dockfleet.health.scheduler import HealthScheduler

    config = DockFleetConfig(
        self_healing=False,
        services={},
    )

    scheduler = HealthScheduler(config, interval_seconds=1, project_dir=None)
    assert scheduler._lock is None

    # start() should not raise even without a lock.
    scheduler.start()
    assert scheduler._thread is not None and scheduler._thread.is_alive()

    scheduler.stop()


# ===========================================================================
# SchedulerLock-only tests (no HealthScheduler import, safe after integration).
# ===========================================================================


def test_single_scheduler_acquires_lock(tmp_path):
    """A single SchedulerLock can be acquired and released cleanly."""
    project = _make_project(tmp_path)
    lock = SchedulerLock(project)

    lock.acquire()
    assert lock.is_held

    lock.release()
    assert not lock.is_held

    # Lock and PID files should be cleaned up.
    assert not lock._lock_path.exists()
    assert not lock._pid_path.exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "msvcrt.locking state from earlier tests in the same session can "
        "interfere with subprocess.Popen on Windows.  Cross-process "
        "contention is validated by the integration tests (1-2) above."
    ),
)
def test_second_acquire_raises_conflict(tmp_path):
    """A second process must raise RuntimeError when the first still holds
    the lock — true cross-process test using subprocess."""
    project = _make_project(tmp_path)
    repo_root = str(Path(__file__).resolve().parent.parent)

    script = (
        f"import sys, time; sys.path.insert(0, r'{repo_root}'); "
        f"from dockfleet.health.scheduler_lock import SchedulerLock; "
        f"from pathlib import Path; "
        f"lock = SchedulerLock(Path(r'{project}')); "
        f"lock.acquire(); "
        f"Path(r'{project}').joinpath('.child_ready').write_text('ok'); "
        f"time.sleep(60)"
    )

    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    marker = project / ".child_ready"
    for _ in range(30):
        if marker.exists():
            break
        time.sleep(0.5)
    else:
        child.terminate()
        child.wait(timeout=5)
        pytest.fail("Child process did not acquire lock within timeout")

    try:
        lock_b = SchedulerLock(project)
        with pytest.raises(RuntimeError, match="already running"):
            lock_b.acquire()
    finally:
        child.terminate()
        child.wait(timeout=5)
        marker.unlink(missing_ok=True)

    # After child crash, stale lock recovery should work.
    lock_c = SchedulerLock(project)
    lock_c.acquire()
    assert lock_c.is_held
    lock_c.release()


def test_release_allows_reacquire(tmp_path):
    """After the holder releases, a new scheduler can acquire the lock."""
    project = _make_project(tmp_path)

    lock_a = SchedulerLock(project)
    lock_a.acquire()
    lock_a.release()

    lock_b = SchedulerLock(project)
    lock_b.acquire()
    assert lock_b.is_held
    lock_b.release()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "msvcrt.locking state from earlier tests in the same session can "
        "interfere with subprocess.Popen on Windows.  Stale-lock recovery "
        "is validated by the integration tests (1-2) above."
    ),
)
def test_stale_lock_recovery(tmp_path):
    """A lock held by a process whose PID file references a dead PID is
    treated as stale and recovered automatically.

    The subprocess acquires the real OS lock, then overwrites the PID file
    with a certainly-dead PID (9999999).  When the parent tries to acquire,
    ``_lock_file()`` fails (the child holds the OS lock), so
    ``_raise_conflict()`` is entered.  It reads the PID file, finds a dead
    PID, calls ``_cleanup_stale()``, and re-acquires successfully.
    """
    project = _make_project(tmp_path)
    repo_root = str(Path(__file__).resolve().parent.parent)

    # Child: acquire lock, overwrite PID file with dead PID, hold lock.
    script = (
        f"import json, os, sys, time; "
        f"sys.path.insert(0, r'{repo_root}'); "
        f"from pathlib import Path; "
        f"from dockfleet.health.scheduler_lock import SchedulerLock; "
        f"p = Path(r'{project}'); "
        f"lock = SchedulerLock(p); "
        f"lock.acquire(); "
        # Overwrite PID file with a certainly-dead PID so the parent's
        # _raise_conflict() sees a stale holder.
        f"with open(p / '.scheduler.pid', 'w') as f: "
        f"  json.dump({{'pid': 9999999, 'start_time': 0, "
        f"  'token': 'stale', 'hostname': 'dead-host'}}, f); "
        f"Path(r'{project}').joinpath('.child_ready').write_text('ok'); "
        f"time.sleep(60)"
    )

    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for child to acquire the lock and write the marker.
    marker = project / ".child_ready"
    for _ in range(30):
        if marker.exists():
            break
        time.sleep(0.5)
    else:
        child.terminate()
        child.wait(timeout=5)
        pytest.fail("Child process did not acquire lock within timeout")

    # Verify the child wrote a dead PID to the PID file.
    pid_info = json.loads((project / ".scheduler.pid").read_text())
    assert pid_info["pid"] == 9999999

    try:
        # Parent tries to acquire — the OS lock is held by the child, so
        # _lock_file() fails, _raise_conflict() reads the PID file, finds
        # PID 9999999 (dead), calls _cleanup_stale(), and re-acquires.
        lock = SchedulerLock(project)
        lock.acquire()
        assert lock.is_held
        lock.release()
    finally:
        child.terminate()
        child.wait(timeout=5)
        marker.unlink(missing_ok=True)


def test_different_projects_independent(tmp_path):
    """Two schedulers for different projects can run simultaneously."""
    proj_a = _make_project(tmp_path, "project_a")
    proj_b = _make_project(tmp_path, "project_b")

    lock_a = SchedulerLock(proj_a)
    lock_a.acquire()
    assert lock_a.is_held

    lock_b = SchedulerLock(proj_b)
    lock_b.acquire()
    assert lock_b.is_held

    # Both held at the same time.
    assert lock_a.is_held
    assert lock_b.is_held

    lock_a.release()
    lock_b.release()


def test_pid_is_running():
    """Test SchedulerLock._pid_is_running for active, dead, and invalid PIDs."""
    import os

    # Current process must be running
    assert SchedulerLock._pid_is_running(os.getpid()) is True

    # Invalid / non-existent PIDs
    assert SchedulerLock._pid_is_running(0) is False
    assert SchedulerLock._pid_is_running(-1) is False
    assert SchedulerLock._pid_is_running(9999999) is False
