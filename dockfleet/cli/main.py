import sys
import subprocess
from pathlib import Path
import logging
import time
from dockfleet.health.status import update_service_health
import typer
from pydantic import ValidationError
from dockfleet.cli.config import load_config
from dockfleet.core.orchestrator import Orchestrator
from dockfleet.health.seed import bootstrap_from_path
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.models import sqlite_file_name  # if you expose this; otherwise hardcode "dockfleet.db"


app = typer.Typer(help="DockFleet CLI - Manage Docker services from YAML configuration")

validate_app = typer.Typer()
app.add_typer(validate_app, name="validate")


@validate_app.callback(invoke_without_command=True)
def validate(path: Path = typer.Argument("dockfleet.yaml")):
    """Validate a DockFleet YAML configuration file before running services."""
    try:
        load_config(path)
        typer.echo("✓ Config valid")

    except ValidationError as e:
        typer.echo("✗ Config validation failed")
        for err in e.errors():
            location = " -> ".join(str(x) for x in err["loc"])
            typer.echo(f"{location}: {err['msg']}")
        raise typer.Exit(code=1)

    except Exception as e:
        typer.echo(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command()
def seed(path: Path = typer.Argument("dockfleet.yaml")):
    """Initialize the service database and register services from the configuration."""
    try:
        typer.echo(f"Seeding services from {path}...")

        bootstrap_from_path(str(path))

        typer.echo("✓ Seeding complete")

    except Exception as e:
        typer.echo(f"Seeding failed: {e}")
        raise typer.Exit(code=1)


@app.command()
def up(path: Path = typer.Argument("dockfleet.yaml")):
    """Start all services defined in the DockFleet configuration."""
    try:
        config = load_config(path)

        typer.echo(f"Starting services from {path}...\n")

        orch = Orchestrator(config)
        orch.up()

        typer.echo("\n✓ Services started")

    except Exception as e:
        typer.echo(f"Error starting services: {e}")
        raise typer.Exit(code=1)


@app.command()
def down(path: Path = typer.Argument("dockfleet.yaml")):
    """Stop and remove all containers managed by DockFleet."""
    try:
        config = load_config(path)

        typer.echo(f"Stopping services from {path}...\n")

        orch = Orchestrator(config)
        orch.down()

        typer.echo("\n✓ Services stopped")

    except Exception as e:
        typer.echo(f"Error stopping services: {e}")
        raise typer.Exit(code=1)


@app.command()
def ps():
    """Show currently running DockFleet containers."""
    try:
        typer.echo("Listing running containers...\n")

        orch = Orchestrator(config=None)
        orch.ps()

    except Exception as e:
        typer.echo(f"Error listing containers: {e}")
        raise typer.Exit(code=1)


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

    except Exception:
        typer.echo("✗ Docker not found or not running")
        raise typer.Exit(code=1)


@app.command("health-dev")
def health_dev(
    path: Path = typer.Argument("dockfleet.yaml"),
    once: bool = typer.Option(
        False,
        "--once",
        help="Run a single health pass and exit (useful for tests).",
    ),
):
    """
    Developer command to run the health check scheduler backed by SQLite DB.

    - Initializes DB + seeds services if needed.
    - Runs HealthScheduler which writes status into the Service table.
    - By default runs in a loop until Ctrl+C; with --once runs one cycle and exits.
    """
    try:
        typer.echo("Starting DockFleet health check scheduler (DEV MODE)")
        typer.echo("Press Ctrl+C to stop\n" if not once else "Running a single health pass\n")

        config = load_config(path)

        # Ensure DB and Service rows are present
        typer.echo(f"Bootstrapping health DB from {path} ...")
        bootstrap_from_path(str(path))

        # Print DB location for debugging
        db_path = sqlite_file_name if "sqlite_file_name" in globals() else "dockfleet.db"
        typer.echo(f"Health engine using SQLite DB at: {db_path}\n")

        # check if any service has healthcheck defined
        services_with_health = [
            name for name, svc in config.services.items()
            if svc.healthcheck is not None
        ]

        if not services_with_health:
            typer.echo("No services with healthcheck defined in config.")
            raise typer.Exit(code=1)

        scheduler = HealthScheduler(config)

        if once:
            scheduler._logger = logging.getLogger(__name__)
            for name, svc_cfg in config.services.items():
                hc = svc_cfg.healthcheck
                if hc is None:
                    continue
                ok = scheduler._run_single_check(name, hc)
                status_str = "HEALTHY" if ok else "UNHEALTHY"
                typer.echo(f"[once] {name} -> {status_str}")
                update_service_health(
                    name,
                    ok,
                    reason=None if ok else "health check failed",
                )
                scheduler._handle_post_health(name)
            typer.echo("Single health pass complete.")
        return


        # Normal long-running mode
        scheduler.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            typer.echo("\nStopping health scheduler...")
            scheduler.stop()

    except Exception as e:
        typer.echo(f"Health scheduler failed: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()