import json
from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine, select

from dockfleet.cli.config import (
    DockFleetConfig,
    HealthCheckConfig,
    ResourcesConfig,
    RestartPolicy,
    ServiceConfig,
)
from dockfleet.health.models import ContainerStatus, HealthStatus, Service, get_session
from dockfleet.health.seed import seed_services


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

    with get_session(engine=engine) as session:
        # first seed
        seed_services(config, session)
        count_after_first = session.exec(select(Service)).all()
        assert len(count_after_first) == 2

        # second seed (should not duplicate)
        seed_services(config, session)
        count_after_second = session.exec(select(Service)).all()
        assert len(count_after_second) == 2


def test_seed_services_updates_existing_config_and_preserves_runtime_state():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    initial_config = make_test_config()

    with get_session(engine=engine) as session:
        seed_services(initial_config, session)

        # Mutate runtime state for 'api'
        api_svc = session.exec(select(Service).where(Service.name == "api")).one()
        api_svc.status = ContainerStatus.RUNNING
        api_svc.health_status = HealthStatus.UNHEALTHY
        api_svc.restart_count = 4
        api_svc.consecutive_failures = 2
        api_svc.last_failure_reason = "HTTP 500"
        check_time = datetime(2026, 9, 19, 10, 0, 0)
        api_svc.last_health_check = check_time
        session.add(api_svc)
        session.commit()

        # Update configuration for 'api' and 'redis'
        updated_config = DockFleetConfig(
            services={
                "api": ServiceConfig(
                    image="my-api:v2.0",
                    ports=["9000:9000", "9001:9001"],
                    healthcheck=HealthCheckConfig(
                        type="http",
                        endpoint="http://localhost:9000/healthz",
                        interval=10,
                    ),
                    restart=RestartPolicy.on_failure,
                    resources=ResourcesConfig(memory="512m", cpu=1.5),
                    environment=["ENV=production", "DEBUG=false"],
                    depends_on=["redis"],
                ),
                "redis": ServiceConfig(
                    image="redis:7.2-alpine",
                    ports=["6379:6379"],
                    healthcheck=HealthCheckConfig(
                        type="tcp",
                        endpoint="6379",
                        interval=15,
                    ),
                    restart=RestartPolicy.never,
                ),
            }
        )

        # Re-run seed_services
        seed_services(updated_config, session)

        # Query updated services
        updated_api = session.exec(select(Service).where(Service.name == "api")).one()

        # Verify config fields updated
        assert updated_api.image == "my-api:v2.0"
        assert updated_api.ports_raw == "9000:9000,9001:9001"
        assert updated_api.restart_policy == "on-failure"
        assert updated_api.resources_memory == "512m"
        assert updated_api.resources_cpu == 1.5
        assert updated_api.env_raw == json.dumps(["ENV=production", "DEBUG=false"])
        assert updated_api.depends_on_raw == "redis"
        hc_data = json.loads(updated_api.healthcheck_raw)
        assert hc_data == {"type": "http", "endpoint": "http://localhost:9000/healthz", "interval": 10}

        # Verify runtime state fields remained preserved
        assert updated_api.status == ContainerStatus.RUNNING
        assert updated_api.health_status == HealthStatus.UNHEALTHY
        assert updated_api.restart_count == 4
        assert updated_api.consecutive_failures == 2
        assert updated_api.last_failure_reason == "HTTP 500"
        assert updated_api.last_health_check == check_time

        # Verify redis updated as well
        updated_redis = session.exec(select(Service).where(Service.name == "redis")).one()
        assert updated_redis.image == "redis:7.2-alpine"
        assert updated_redis.ports_raw == "6379:6379"
        assert updated_redis.restart_policy == "never"

