import typer
import sys
import subprocess
from pathlib import Path
from pydantic import ValidationError

from dockfleet.cli.config import load_config
from dockfleet.core.orchestrator import Orchestrator
from dockfleet.health.seed import bootstrap_from_path
from dockfleet.health.scheduler import HealthScheduler

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
            check=True
        )

        typer.echo(f"Docker detected: {result.stdout.strip()}")
        typer.echo("✓ Environment looks good")

    except Exception:
        typer.echo("✗ Docker not found or not running")
        raise typer.Exit(code=1)
@app.command("health-dev")
def health_dev(path: Path = typer.Argument("dockfleet.yaml")):
    """
    Developer command to run the health check scheduler manually.
    Press Ctrl+C to stop.
    """

    try:
        typer.echo("Starting DockFleet health check scheduler (DEV MODE)")
        typer.echo("Press Ctrl+C to stop\n")

        config = load_config(path)

        # check if any service has healthcheck defined
        services_with_health = [
            name for name, svc in config.services.items()
            if svc.healthcheck is not None
        ]

        if not services_with_health:
            typer.echo("No services with healthcheck defined in config.")
            raise typer.Exit(code=1)

        scheduler = HealthScheduler(config)

        scheduler.start()

        try:
            while True:
                pass
        except KeyboardInterrupt:
            typer.echo("\nStopping health scheduler...")
            scheduler.stop()

    except Exception as e:
        typer.echo(f"Health scheduler failed: {e}")
        raise typer.Exit(code=1)

if __name__ == "__main__":
    app()