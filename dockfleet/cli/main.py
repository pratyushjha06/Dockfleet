import typer
from pathlib import Path
from pydantic import ValidationError
from dockfleet.cli.config import load_config

app = typer.Typer()

validate_app = typer.Typer()
app.add_typer(validate_app, name="validate")

@validate_app.callback(invoke_without_command=True)
def validate(path: Path = typer.Argument("dockfleet.yaml")):
    """Validate a DockFleet configuration file."""
    try:
        load_config(path)
        typer.echo("Config valid")

    except ValidationError as e:
        typer.echo("Config validation failed")
        for err in e.errors():
            location = " -> ".join(str(x) for x in err["loc"])
            typer.echo(f"{location}: {err['msg']}")
        raise typer.Exit(code=1)

    except Exception as e:
        typer.echo(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

if __name__ == "__main__":
    app()