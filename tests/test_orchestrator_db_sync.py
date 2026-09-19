from unittest.mock import MagicMock, patch
from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, load_config
from dockfleet.core.orchestrator import Orchestrator
from dockfleet.health.models import ContainerStatus, Service, get_session, init_db
from dockfleet.health.services import seed_services


def test_orchestrator_updates_db_status(tmp_path):
    """
    End-to-end check of DB sync:
    - init DB + seed services from YAML
    - orchestrator.up() runs
    - orchestrator.down() stops running ones
    """

    init_db()

    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    with get_session() as session:
        seed_services(config, session)

    # Baseline: after seed, all services have some initial status
    with get_session() as session:
        services = session.exec(select(Service)).all()
        assert len(services) > 0

    orch = Orchestrator(config)
    with patch.object(orch.docker, "run_container"), patch.object(
        orch.docker, "stop_container"
    ), patch.object(orch.docker, "remove_container"), patch.object(
        orch.docker, "create_network"
    ):
        orch.up()

        # After up: seeded services should be marked RUNNING
        with get_session() as session:
            services = {svc.name: svc for svc in session.exec(select(Service)).all()}
            assert services["db"].status == ContainerStatus.RUNNING
            assert services["api"].status == ContainerStatus.RUNNING

        orch.down()

        # After down: all services should end up STOPPED
        with get_session() as session:
            services = session.exec(select(Service)).all()
            for svc in services:
                assert svc.status == ContainerStatus.STOPPED
