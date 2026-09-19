from dockfleet.cli.config import DockFleetConfig, load_config
from dockfleet.health.models import get_session, init_db
from dockfleet.health.services import seed_services


def bootstrap_from_config(config: DockFleetConfig) -> None:
    """Initialize SQLite database schema and seed services from a configuration object."""
    # 1) Ensure DB and tables exist
    init_db()

    # 2) Open session and seed services (idempotent)
    with get_session() as session:
        seed_services(config, session)


def bootstrap_from_path(config_path: str = "examples/dockfleet.yaml") -> None:
    """Load configuration from YAML path and bootstrap database services."""
    # Load YAML → DockFleetConfig
    config = load_config(config_path)

    # Delegate to the in-memory bootstrap
    bootstrap_from_config(config)


def main() -> None:
    """CLI entrypoint for standalone database seeding."""
    # For now, just call bootstrap with default example path.
    # Later, CLI can pass a custom path or integrate with `dockfleet up`.
    bootstrap_from_path()


if __name__ == "__main__":
    main()
