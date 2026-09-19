# dockfleet/health/metrics.py
from sqlmodel import select

from dockfleet.health.models import ContainerStatus, Service, get_session


def get_total_restarts() -> int:
    """
    Total restart count across all services, based on Service.restart_count.
    """
    with get_session() as session:
        services = session.exec(select(Service)).all()
        return sum(s.restart_count or 0 for s in services)


def get_running_services_count() -> int:
    """
    Count how many services are currently running.
    """
    with get_session() as session:
        services = session.exec(select(Service)).all()
        return sum(1 for s in services if s.status == ContainerStatus.RUNNING)


def get_health_failures_count() -> int:
    """
    Approximate recent health failures using consecutive_failures on Service.
    """
    with get_session() as session:
        services = session.exec(select(Service)).all()
        return sum(s.consecutive_failures or 0 for s in services)
