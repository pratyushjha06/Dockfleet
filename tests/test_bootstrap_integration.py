from sqlmodel import Session, select
from dockfleet.cli.config import DockFleetConfig, load_config
from dockfleet.health.models import Service, init_db, engine
from dockfleet.health.seed import bootstrap_from_config


def test_bootstrap_from_config_seeds_services(tmp_path):
    """
    Integration-style test for YAML -> DockFleetConfig -> SQLite services table.

    This does NOT start Docker containers yet; orchestrator's `up(config)`
    can be plugged into the same pattern later.
    """
    # Arrange: load example config (or point to a test YAML under tests/data)
    config: DockFleetConfig = load_config("examples/dockfleet.yaml")

    # Act: init DB + seed via bootstrap
    init_db()
    bootstrap_from_config(config)

    # Assert: services table has one row per service in config
    with Session(engine) as session:
        services_in_db = session.exec(select(Service)).all()

    # Number of services configured in YAML
    expected_count = len(config.services)

    assert len(services_in_db) == expected_count

    names_in_db = {s.name for s in services_in_db}
    expected_names = set(config.services.keys())
    assert names_in_db == expected_names
