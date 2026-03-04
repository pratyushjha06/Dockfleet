from sqlmodel import Session
from dockfleet.cli.config import load_config, DockFleetConfig
from dockfleet.health.models import init_db, engine
from dockfleet.health.services import seed_services

def bootstrap_from_config(config: DockFleetConfig) -> None:
    # 1) Ensure DB and tables exist
    init_db()

    # 2) Open session and seed services (idempotent)
    with Session(engine) as session:
        seed_services(config, session)

def bootstrap_from_path(config_path: str = "examples/dockfleet.yaml") -> None:
    # Load YAML → DockFleetConfig
    config = load_config(config_path)

    # Delegate to the in-memory bootstrap
    bootstrap_from_config(config)

def main() -> None:
    # For now, just call bootstrap with default example path.
    # Later, CLI can pass a custom path or integrate with `dockfleet up`.
    bootstrap_from_path()


if __name__ == "__main__":
    main()
