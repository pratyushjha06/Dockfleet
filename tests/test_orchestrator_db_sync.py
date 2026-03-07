from sqlmodel import Session, select

from dockfleet.health.models import init_db, Service, engine
from dockfleet.health.services import seed_services
from dockfleet.cli.config import load_config, DockFleetConfig
from dockfleet.core.orchestrator import Orchestrator


def test_orchestrator_updates_db_status(tmp_path):
    """
    End-to-end check of DB sync:
    - init DB + seed services from YAML
    - orchestrator.up() runs (some services may fail to start)
    - orchestrator.down() stops running ones
    """

    init_db()

    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    with Session(engine) as session:
        seed_services(config, session)

    # Baseline: after seed, all services have some initial status
    with Session(engine) as session:
        services = session.exec(select(Service)).all()
        assert len(services) > 0

    orch = Orchestrator(config)
    orch.up()

    # After up:
    # - redis (valid image) should be 'running'
    # - api (missing image) should remain 'stopped' (or whatever baseline was)
    with Session(engine) as session:
        services = {svc.name: svc for svc in session.exec(select(Service)).all()}

        # redis successfully pulled & started according to stdout
        assert services["redis"].status == "running"

        # api failed to start, so it should NOT be 'running'
        assert services["api"].status != "running"

    orch.down()

    # After down: all services should end up 'stopped'
    with Session(engine) as session:
        services = session.exec(select(Service)).all()
        for svc in services:
            assert svc.status == "stopped"
