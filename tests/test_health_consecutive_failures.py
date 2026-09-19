from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, load_config
from dockfleet.health.models import Service, get_session, init_db
from dockfleet.health.services import seed_services
from dockfleet.health.status import update_service_health


def test_consecutive_failures_and_status_transitions(tmp_path):
    """
    sanity check on update_service_health:
    Cycle 1: healthy   -> status='running', health_status='healthy', consecutive_failures=0, restart_count unchanged
    Cycle 2: unhealthy -> status stays 'running', health_status='crashed', consecutive_failures=1, restart_count unchanged
    Cycle 3: unhealthy -> status stays 'running', health_status='crashed', consecutive_failures=2, restart_count unchanged

    restart_count only increments on an actual restart (handled elsewhere,
    e.g. by the orchestrator/health scheduler after the failure threshold
    is hit), not on every individual failed health check.
    """
    # 1) Fresh DB schema
    init_db()

    # 2) Load config + seed
    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    with get_session() as session:
        seed_services(config, session)

    service_name = list(config.services.keys())[0]

    # Helper to fetch fresh row
    def get_service():
        with get_session() as session_local:
            return session_local.exec(
                select(Service).where(Service.name == service_name)
            ).one()

    # Baseline (after seed)
    baseline = get_service()
    baseline_restart_count = baseline.restart_count

    # Cycle 1: healthy
    update_service_health(service_name, is_healthy=True, reason=None)
    svc = get_service()
    assert svc.status == "running"
    assert svc.consecutive_failures == 0
    assert svc.restart_count == baseline_restart_count

    # Cycle 2: unhealthy
    update_service_health(service_name, is_healthy=False, reason="fail 1")
    svc = get_service()
    assert svc.status == "running"
    assert svc.health_status == "unhealthy"
    assert svc.consecutive_failures == 1
    assert svc.restart_count == baseline_restart_count
    assert svc.last_failure_reason == "fail 1"

    # Cycle 3: unhealthy again
    update_service_health(service_name, is_healthy=False, reason="fail 2")
    svc = get_service()
    assert svc.status == "running"
    assert svc.health_status == "unhealthy"
    assert svc.consecutive_failures == 2
    assert svc.restart_count == baseline_restart_count
    assert svc.last_failure_reason == "fail 2"

    # Cycle 4: 3rd consecutive failure -> reaches threshold for CRASHED
    update_service_health(service_name, is_healthy=False, reason="fail 3")
    svc = get_service()
    assert svc.status == "running"
    assert svc.health_status == "crashed"
    assert svc.consecutive_failures == 3
    assert svc.restart_count == baseline_restart_count
    assert svc.last_failure_reason == "fail 3"
