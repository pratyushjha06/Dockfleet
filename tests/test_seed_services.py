import pytest
from sqlmodel import SQLModel, create_engine, Session, select
from dockfleet.health.models import Service
from dockfleet.health.seed import seed_services
from dockfleet.cli.config import DockFleetConfig, ServiceConfig, HealthCheckConfig, RestartPolicy


def make_test_config() -> DockFleetConfig:
    return DockFleetConfig(
        services={
            "api": ServiceConfig(
                image="my-api:latest",
                ports=["8000:8000"],
                healthcheck=HealthCheckConfig(
                    type="http",
                    endpoint="http://localhost:8000/health",
                    interval=30,
                ),
                restart=RestartPolicy.always,
            ),
            "redis": ServiceConfig(
                image="redis:7",
                ports=None,
                healthcheck=None,
                restart=RestartPolicy.always,
            ),
        }
    )


def test_seed_services_idempotent():
    # in‑memory SQLite engine
    engine = create_engine("sqlite://")

    # create tables in memory
    SQLModel.metadata.create_all(engine)

    config = make_test_config()

    with Session(engine) as session:
        # first seed
        seed_services(config, session)
        count_after_first = session.exec(select(Service)).all()
        assert len(count_after_first) == 2

        # second seed (should not duplicate)
        seed_services(config, session)
        count_after_second = session.exec(select(Service)).all()
        assert len(count_after_second) == 2
