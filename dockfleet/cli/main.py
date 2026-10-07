import importlib.metadata
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import typer
from pydantic import ValidationError
from sqlmodel import select

from dockfleet.cli.config import load_config
from dockfleet.core.orchestrator import Orchestrator
from dockfleet.health.logs import LogEvent
from dockfleet.health.models import PROJECT_ROOT, get_session
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.scheduler_lock import SchedulerLock
from dockfleet.health.seed import bootstrap_from_path

app = typer.Typer(help="DockFleet CLI - Manage Docker services from YAML configuration")
validate_app = typer.Typer()
app.add_typer(validate_app, name="validate")


def spawn_background_scheduler(config_path: Path | str) -> subprocess.Popen:
    """
    Launch `dockfleet self-heal <config_path>` as a detached background process.
    """
    cmd = [
        sys.executable,
        "-m",
        "dockfleet.cli.main",
        "self-heal",
        str(config_path),
    ]
    kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS (0x8) | CREATE_NEW_PROCESS_GROUP (0x200)
        kwargs["creationflags"] = 0x00000008 | 0x00000200
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
        kwargs["close_fds"] = True

    return subprocess.Popen(cmd, **kwargs)


def stop_background_scheduler(project_dir: Path | str = PROJECT_ROOT) -> bool:
    """
    Attempt to stop a running background scheduler process by reading .scheduler.pid.
    Handles process termination across both POSIX and Windows operating systems.
    Deletes all temporary lock and PID tracking files (.scheduler.lock, .scheduler.pid).
    """
    p = Path(project_dir).resolve()
    if p.is_file() or p.suffix in (".db", ".yaml", ".yml", ".json"):
        project_path = p.parent
    else:
        project_path = p
    pid_file = project_path / SchedulerLock.PID_FILENAME
    lock_file = project_path / SchedulerLock.LOCK_FILENAME
    stopped = False

    if pid_file.exists():
        try:
            info = json.loads(pid_file.read_text(encoding="utf-8"))
            pid = info.get("pid")
            if pid and SchedulerLock._pid_is_running(pid):
                if sys.platform == "win32":
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (OSError, PermissionError):
                        import ctypes

                        PROCESS_TERMINATE = 0x0001
                        kernel32 = ctypes.windll.kernel32
                        h_proc = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                        if h_proc:
                            try:
                                kernel32.TerminateProcess(h_proc, 1)
                            finally:
                                kernel32.CloseHandle(h_proc)
                        else:
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(pid)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                else:
                    os.kill(pid, signal.SIGTERM)
                stopped = True
        except Exception:
            pass

    for path in (pid_file, lock_file):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    return stopped


def version_callback(value: bool):
    if value:
        try:
            version = importlib.metadata.version("dockfleet")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        typer.echo(f"DockFleet version {version}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the installed DockFleet version and exit.",
    ),
):
    pass


# ------------------------------------------------
# Logging setup for health scheduler
# ------------------------------------------------

HEALTH_LOG_PATH = Path("dockfleet-health.log")


def setup_health_logging() -> None:
    """
    Configure logging so health scheduler logs go only to dockfleet-health.log.
    Call this before starting HealthScheduler.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(HEALTH_LOG_PATH, encoding="utf-8"),
        ],
    )


# ------------------------------------------------
# validate
# ------------------------------------------------


@validate_app.callback(invoke_without_command=True)
def validate(path: Path = typer.Argument("examples/dockfleet.yaml")):
    """Validate a DockFleet YAML configuration file before running services."""
    try:
        load_config(path)
        typer.echo("✓ Config valid")
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# seed
# ------------------------------------------------


@app.command()
def seed(path: Path = typer.Argument("examples/dockfleet.yaml")):
    """Initialize the service database and register services from the configuration."""
    try:
        typer.echo(f"Seeding services from {path}...")
        bootstrap_from_path(str(path))
        typer.echo("✓ Seeding complete")
    except Exception as e:
        typer.echo(f"Seeding failed: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# up
# ------------------------------------------------


@app.command()
def up(
    path: Path = typer.Argument("examples/dockfleet.yaml"),
    detach: bool = typer.Option(
        True,
        "--detach/--foreground",
        "-d/-f",
        help="Run health scheduler as a detached background process (default) or in foreground.",
    ),
):
    """
    Start all services and the health engine (self-healing mode).

    - Bootstraps health DB from YAML.
    - Starts services via Orchestrator.
    - Starts HealthScheduler in background detached process (or foreground).
    - Logs go to dockfleet-health.log.
    """
    try:
        # Load config
        config = load_config(path)

        typer.echo(f"Starting services from {path}...\n")

        # Ensure DB has Service rows for this config
        typer.echo(f"Bootstrapping health DB from {path} ...")
        bootstrap_from_path(str(path))

        # Start orchestrator (non-blocking orchestration only)
        orch = Orchestrator(config)
        orch.up()

        typer.echo("Services started.")

        if detach:
            # Check if health scheduler is already running
            lock = SchedulerLock(PROJECT_ROOT)
            pid_info = lock._read_pid_file()
            if pid_info and lock._pid_is_running(pid_info.get("pid", -1)):
                typer.echo(
                    f"Health scheduler is already running in background (PID {pid_info['pid']})."
                )
            else:
                spawn_background_scheduler(str(path))
                typer.echo(
                    f"Health scheduler started in background; logs -> {HEALTH_LOG_PATH}\n"
                )
            typer.echo("Use `dockfleet health-logs` to inspect health engine output.")
        else:
            setup_health_logging()
            scheduler = HealthScheduler(config, project_dir=PROJECT_ROOT)
            typer.echo(
                f"Running health scheduler in foreground (Ctrl+C to stop); logs -> {HEALTH_LOG_PATH}\n"
            )
            scheduler.start()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                typer.echo("\nStopping health scheduler...")
                scheduler.stop()
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error starting services: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# down
# ------------------------------------------------


@app.command()
def down(path: Path = typer.Argument("examples/dockfleet.yaml")):
    """Stop and remove all containers managed by DockFleet."""
    try:
        config = load_config(path)

        typer.echo(f"Stopping services from {path}...\n")

        orch = Orchestrator(config)
        orch.down()

        # Stop background scheduler if running and delete temporary lock/pid files
        stop_background_scheduler(path)

        typer.echo("\n✓ Services stopped")
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error stopping services: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# restart
# ------------------------------------------------
@app.command()
def restart(path: Path = typer.Argument("examples/dockfleet.yaml")):
    """Restart all services managed by DockFleet."""
    try:
        config = load_config(path)

        typer.echo(f"Restarting services from {path}...\n")

        # Stop background scheduler if running
        stop_background_scheduler(PROJECT_ROOT)

        # Ensure DB has Service rows for this config
        typer.echo(f"Bootstrapping health DB from {path} ...")
        bootstrap_from_path(str(path))

        orch = Orchestrator(config)
        orch.restart()

        # Respawn background scheduler
        spawn_background_scheduler(str(path))
        typer.echo(
            f"Health scheduler started in background; logs -> {HEALTH_LOG_PATH}\n"
        )
        typer.echo("Use `dockfleet health-logs` to inspect health engine output.")
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error restarting services: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# ps
# ------------------------------------------------


@app.command()
def ps(
    path: Path = typer.Argument("examples/dockfleet.yaml"),
    json_output: bool = typer.Option(
        False, "--json", help="Output container status in JSON format"
    ),
):
    """Show currently running DockFleet containers."""
    try:
        if not json_output:
            typer.echo("Listing running containers...\n")

        config = load_config(path)
        orch = Orchestrator(config)
        orch.ps(json_output=json_output)
    except typer.Exit:
        raise
    except Exception as e:
        if json_output:
            typer.echo(f"Error listing containers: {e}", err=True)
        else:
            typer.echo(f"Error listing containers: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# logs (docker logs)
# ------------------------------------------------


@app.command()
def logs(
    service: str = typer.Argument(..., help="Service name"),
    lines: int = typer.Option(100, "--lines", help="Number of log lines"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
):
    """
    Show raw Docker logs for a DockFleet service container.
    """
    container_name = f"dockfleet_{service}"

    try:
        inspect_res = subprocess.run(
            ["docker", "inspect", container_name],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if inspect_res.returncode != 0:
            typer.echo(f"Service '{service}' not found or container not running.")
            raise typer.Exit(code=1)

        if follow:
            typer.echo(f"Streaming logs for {service} (Ctrl+C to stop)\n")
            try:
                result = subprocess.run(
                    ["docker", "logs", "-f", "--tail", str(lines), container_name]
                )
            except KeyboardInterrupt:
                raise typer.Exit(code=0)

            interrupt_codes = {0, 130, 0xC000013A, 3221225786, -1073741510}
            if hasattr(signal, "SIGINT"):
                interrupt_codes.add(-signal.SIGINT)
                interrupt_codes.add(signal.SIGINT)

            if result.returncode in interrupt_codes:
                raise typer.Exit(code=0)

            typer.echo(f"Service '{service}' not found or container not running.")
            raise typer.Exit(code=1)
        else:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(lines), container_name],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout_str = (
                result.stdout.decode("utf-8", errors="replace")
                if isinstance(result.stdout, bytes)
                else (result.stdout or "")
            )
            stderr_str = (
                result.stderr.decode("utf-8", errors="replace")
                if isinstance(result.stderr, bytes)
                else (result.stderr or "")
            )
            if result.returncode != 0:
                err_msg = (
                    stderr_str.strip()
                    if stderr_str and stderr_str.strip()
                    else f"Service '{service}' not found or container not running."
                )
                typer.echo(err_msg)
                raise typer.Exit(code=1)
            typer.echo(stdout_str)
    except KeyboardInterrupt:
        raise typer.Exit(code=0)
    except typer.Exit:
        raise
    except Exception:
        typer.echo(f"Service '{service}' not found or container not running.")
        raise typer.Exit(code=1)


# ------------------------------------------------
# show-logs (DB logs)
# ------------------------------------------------


@app.command("show-logs")
def show_logs(
    service: str = typer.Option(None, "--service", help="Filter by service name"),
    limit: int = typer.Option(10, "--limit", help="Number of logs to show"),
):
    """
    Show aggregated logs stored in DockFleet database.
    """
    try:
        with get_session() as session:
            query = select(LogEvent)

            if service:
                query = query.where(LogEvent.service_name == service)

            query = query.order_by(
                LogEvent.created_at.desc(), LogEvent.id.desc()
            ).limit(limit)

            logs = session.exec(query).all()

            if not logs:
                typer.echo("No logs found.")
                return

            for log in logs:
                ts = getattr(log, "timestamp", None) or getattr(log, "created_at", None)
                if ts is None:
                    ts_str = "no-time"
                elif isinstance(ts, datetime):
                    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    # SQLite may return created_at as an ISO 8601 string instead of a
                    # datetime object (e.g. legacy rows, dialect quirks). Parsing and
                    # re-formatting guards against AttributeError: 'str' has no
                    # attribute 'strftime'. Fall back to the raw string on parse error.
                    try:
                        ts_str = datetime.fromisoformat(str(ts)).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    except (ValueError, TypeError):
                        ts_str = str(ts)

                typer.echo(f"[{ts_str}] [{log.service_name}] {log.message}")
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Failed to fetch logs: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# doctor
# ------------------------------------------------


@app.command()
def doctor():
    """Check system environment (Python version and Docker availability)."""
    typer.echo("Running DockFleet doctor...\n")

    # Python version check
    version = sys.version.split()[0]
    typer.echo(f"Python version: {version}")

    # Docker check
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        typer.echo(f"Docker detected: {result.stdout.strip()}")
        typer.echo("✓ Environment looks good")
    except typer.Exit:
        raise
    except Exception:
        typer.echo("✗ Docker not found or not running")
        raise typer.Exit(code=1)


# ------------------------------------------------
# health-dev (unchanged behavior, for dev)
# ------------------------------------------------


@app.command("health-dev")
def health_dev(
    path: Path = typer.Argument("examples/dockfleet.yaml"),
    once: bool = typer.Option(
        False,
        "--once",
        help="Run a single health pass and exit (useful for tests).",
    ),
    no_restart: bool = typer.Option(
        False,
        "--no-restart",
        help="Run health checks without triggering container restarts.",
    ),
):
    """
    Developer command to run the health check scheduler backed by SQLite DB.
    """
    try:
        typer.echo("Starting DockFleet health check scheduler (DEV MODE)")
        typer.echo(
            "Press Ctrl+C to stop\n" if not once else "Running a single health pass\n"
        )

        config = load_config(path)
        if no_restart:
            config.self_healing = False
            for svc in config.services.values():
                svc.self_healing = False

        # Ensure DB and Service rows are present
        typer.echo(f"Bootstrapping health DB from {path} ...")
        bootstrap_from_path(str(path))

        typer.echo("Health engine using default SQLite DB\n")

        # check if any service has healthcheck defined
        services_with_health = [
            name for name, svc in config.services.items() if svc.healthcheck is not None
        ]

        if not services_with_health:
            typer.echo("No services with healthcheck defined in config.")
            raise typer.Exit(code=1)

        # For --once mode, skip locking (single pass, no long-running scheduler).
        # For long-running mode, lock to prevent duplicate schedulers.
        # Lock scope: PROJECT_ROOT (where dockfleet.db lives).
        project_dir = PROJECT_ROOT if not once else None
        scheduler = HealthScheduler(config, project_dir=project_dir)

        if once:
            results = scheduler.run_single_pass()
            for name, ok in results.items():
                status_str = "HEALTHY" if ok else "UNHEALTHY"
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                typer.echo(f"[{timestamp}] {name}: {status_str.lower()}")
            typer.echo("Single health pass complete.")
            if not all(results.get(svc, False) for svc in services_with_health):
                raise typer.Exit(code=1)
            return

        # Normal long-running mode
        typer.echo("Health monitoring started...")

        scheduler.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            typer.echo("\nStopping health scheduler...")
            scheduler.stop()
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Health scheduler failed: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# self-heal (unchanged; continuous health loop only)
# ------------------------------------------------


@app.command("self-heal")
def self_heal(
    path: Path = typer.Argument("examples/dockfleet.yaml"),
):
    """
    Run DockFleet in continuous self-healing mode (health checks only).
    """
    try:
        setup_health_logging()
        typer.echo("Starting DockFleet self-healing loop...\n")

        config = load_config(path)

        typer.echo(f"Bootstrapping health DB from {path} ...")
        bootstrap_from_path(str(path))

        project_dir = PROJECT_ROOT
        scheduler = HealthScheduler(config, project_dir=project_dir)

        typer.echo("Self-healing active. Press Ctrl+C to stop.\n")

        scheduler.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            typer.echo("\nStopping self-healing loop...")
            scheduler.stop()
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Self-heal command failed: {e}")
        raise typer.Exit(code=1)


# ------------------------------------------------
# health-logs (NEW)
# ------------------------------------------------


@app.command("health-logs")
def health_logs(
    follow: bool = typer.Option(True, "--follow/--no-follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of last lines to show"),
):
    """
    Show health scheduler logs from dockfleet-health.log.
    """
    log_path = HEALTH_LOG_PATH

    if not log_path.exists():
        typer.echo("No health log file found yet.")
        raise typer.Exit(code=1)

    content = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    tail = content[-lines:]
    for line in tail:
        typer.echo(line)

    if not follow:
        return

    typer.echo("\n-- following dockfleet-health.log (Ctrl+C to stop) --")
    last_size = log_path.stat().st_size if log_path.exists() else 0
    try:
        while True:
            time.sleep(1)
            if not log_path.exists():
                last_size = 0
                continue
            new_size = log_path.stat().st_size
            if new_size < last_size:
                last_size = 0
            if new_size > last_size:
                with log_path.open("r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_size)
                    for line in f:
                        typer.echo(line.rstrip("\n"))
                last_size = new_size
    except KeyboardInterrupt:
        typer.echo("\nStopped following health logs.")


if __name__ == "__main__":
    app()
