from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, load_config
from dockfleet.health.models import Service, get_session, init_db
from dockfleet.health.services import seed_services
from dockfleet.health.status import update_service_health


def test_update_service_health_changes_db_fields(tmp_path):
    """
    - seed one service
    - mark it healthy, then unhealthy
    - verify status, last_health_check, restart_count in DB
    """
    # 1) Fresh DB schema
    init_db()

    # 2) Load config and seed services
    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    with get_session() as session:
        seed_services(config, session)
        for s in session.exec(select(Service)).all():
            s.status = "running"
            session.add(s)
        session.commit()

    service_name = list(config.services.keys())[0]

    # 3) Healthy update -> status 'running', last_health_check set
    update_service_health(service_name, is_healthy=True, reason=None)

    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == service_name)).one()
        assert svc.status == "running"
        assert svc.last_health_check is not None
        healthy_restart_count = svc.restart_count

    # 4) Unhealthy update -> status 'unhealthy', restart_count + 1, reason stored
    update_service_health(
        service_name,
        is_healthy=False,
        reason="test failure",
    )

    with get_session() as session:
        svc = session.exec(select(Service).where(Service.name == service_name)).one()

        assert svc.status == "running"
        assert svc.health_status == "unhealthy"
        assert svc.last_health_check is not None
        assert svc.restart_count == healthy_restart_count
        assert svc.last_failure_reason == "test failure"
