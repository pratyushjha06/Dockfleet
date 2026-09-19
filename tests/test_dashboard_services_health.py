import json
from unittest.mock import MagicMock, patch

from sqlmodel import Session, SQLModel, create_engine

from dockfleet.dashboard.services import get_services
from dockfleet.health.models import ContainerStatus, HealthStatus, get_session
from dockfleet.health.models import Service as DBService


def test_get_services_preserves_unhealthy_status(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc_unhealthy = DBService(
            name="web_unhealthy",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.UNHEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        svc_crashed = DBService(
            name="web_crashed",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.CRASHED,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=1,
        )
        svc_restarting = DBService(
            name="web_restarting",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.RESTARTING,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=2,
        )
        svc_healthy = DBService(
            name="web_healthy",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        session.add_all([svc_unhealthy, svc_crashed, svc_restarting, svc_healthy])
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )

    # Mock docker ps returning containers in Up state
    docker_ps_output = "\n".join(
        [
            json.dumps(
                {
                    "Names": "dockfleet_web_unhealthy",
                    "Status": "Up 5 minutes",
                    "RunningFor": "5 minutes",
                }
            ),
            json.dumps(
                {
                    "Names": "dockfleet_web_crashed",
                    "Status": "Up 2 minutes",
                    "RunningFor": "2 minutes",
                }
            ),
            json.dumps(
                {
                    "Names": "dockfleet_web_restarting",
                    "Status": "Up 10 seconds",
                    "RunningFor": "10 seconds",
                }
            ),
            json.dumps(
                {
                    "Names": "dockfleet_web_healthy",
                    "Status": "Up 10 minutes",
                    "RunningFor": "10 minutes",
                }
            ),
        ]
    )

    def mock_subprocess_run(cmd, *args, **kwargs):
        mock_res = MagicMock()
        if "ps" in cmd:
            mock_res.stdout = docker_ps_output
        elif "stats" in cmd:
            mock_res.stdout = ""
        return mock_res

    with patch("subprocess.run", side_effect=mock_subprocess_run):
        services = get_services()

    services_by_name = {s["name"]: s for s in services}

    assert (
        services_by_name["web_unhealthy"]["health_status"]
        == HealthStatus.UNHEALTHY.value
    )
    assert services_by_name["web_unhealthy"]["status"] == ContainerStatus.RUNNING.value

    assert (
        services_by_name["web_crashed"]["health_status"] == HealthStatus.CRASHED.value
    )
    assert services_by_name["web_crashed"]["status"] == ContainerStatus.RUNNING.value

    assert (
        services_by_name["web_restarting"]["health_status"]
        == HealthStatus.RESTARTING.value
    )
    assert services_by_name["web_restarting"]["status"] == ContainerStatus.RUNNING.value

    assert (
        services_by_name["web_healthy"]["health_status"] == HealthStatus.HEALTHY.value
    )
    assert services_by_name["web_healthy"]["status"] == ContainerStatus.RUNNING.value


def test_get_services_handles_none_and_non_string_names(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc = DBService(
            name="web",
            status=ContainerStatus.STOPPED,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        session.add(svc)
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )

    # Various edge case container names in ps output: None, missing, list, non-dockfleet
    docker_ps_output = "\n".join([
        json.dumps({"Names": None, "Status": "Up 5 minutes"}),
        json.dumps({"OtherKey": "value"}),
        json.dumps({"Names": ["dockfleet_web"], "Status": "Up 5 minutes", "RunningFor": "5 minutes"}),
        json.dumps({"Names": ["other_container"], "Status": "Up 5 minutes"}),
        json.dumps({"Names": [], "Status": "Up 5 minutes"}),
        json.dumps({"Names": 12345, "Status": "Up 5 minutes"}),
    ])

    docker_stats_output = "\n".join([
        json.dumps({"Name": None, "CPUPerc": "1.5%", "MemUsage": "50MB"}),
        json.dumps({"Name": ["dockfleet_web"], "CPUPerc": "2.0%", "MemUsage": "60MB"}),
        json.dumps({"OtherKey": "val"}),
    ])

    def mock_subprocess_run(cmd, *args, **kwargs):
        mock_res = MagicMock()
        if "ps" in cmd:
            mock_res.stdout = docker_ps_output
        elif "stats" in cmd:
            mock_res.stdout = docker_stats_output
        return mock_res

    with patch("subprocess.run", side_effect=mock_subprocess_run):
        services = get_services()

    assert len(services) == 1
    assert services[0]["name"] == "web"
    assert services[0]["status"] == ContainerStatus.RUNNING.value
    assert services[0]["cpu"] == "2.0%"
    assert services[0]["memory"] == "60MB"
