from sqlmodel import Session
from dockfleet.cli.config import load_config
from dockfleet.health.models import init_db, engine
from dockfleet.health.services import seed_services

def bootstrap_from_config(config_path: str = "examples/dockfleet.yaml") -> None:
    # 1) Load YAML → DockFleetConfig
    config = load_config(config_path)

    # 2) Ensure DB and tables exist
    init_db()

    # 3) Open session and seed services
    with Session(engine) as session:
        seed_services(config, session)

def main() -> None:
    # For now, just calling bootstrap with default example path.
    # Later, CLI can pass a custom path or integrate with `dockfleet up`.
    bootstrap_from_config()

if __name__ == "__main__":
    main()
