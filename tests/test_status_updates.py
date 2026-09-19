from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, load_config
from dockfleet.health.models import ContainerStatus, Service, get_session, init_db
from dockfleet.health.services import seed_services
from dockfleet.health.status import mark_service_running, mark_service_stopped


def test_mark_service_running_and_stopped(tmp_path):
    """
    End-to-end:
    - load config
    - seed services into SQLite
    - mark one service running, then stopped
    - verify status changes in DB
    """

    # 1) Fresh DB schema
    init_db()
    with get_session() as session:
        for s in session.exec(select(Service)).all():
            session.delete(s)
        session.commit()

    # 2) Config load
    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    # 3) Seed services
    with get_session() as session:
        seed_services(config, session)

    service_name = list(config.services.keys())[0]

    # 4) Status initially STOPPED
    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == service_name)).one()
        assert svc.status == ContainerStatus.STOPPED

    # 5) Mark running
    mark_service_running(service_name)

    # 6) Verify running in DB
    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == service_name)).one()
        assert svc.status == ContainerStatus.RUNNING

    # 7) Mark stopped
    mark_service_stopped(service_name)

    # 8) Verify stopped in DB
    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == service_name)).one()
        assert svc.status == ContainerStatus.STOPPED
