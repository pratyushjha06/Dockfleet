import asyncio
import json
from unittest.mock import MagicMock, patch

import httpx
from httpx import ASGITransport
from sqlmodel import Session, SQLModel, create_engine, select

from dockfleet.dashboard.api import app
from dockfleet.dashboard.routes import get_metrics, system_status
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
    docker_ps_output = "\n".join(
        [
            json.dumps({"Names": None, "Status": "Up 5 minutes"}),
            json.dumps({"OtherKey": "value"}),
            json.dumps(
                {
                    "Names": ["dockfleet_web"],
                    "Status": "Up 5 minutes",
                    "RunningFor": "5 minutes",
                }
            ),
            json.dumps({"Names": ["other_container"], "Status": "Up 5 minutes"}),
            json.dumps({"Names": [], "Status": "Up 5 minutes"}),
            json.dumps({"Names": 12345, "Status": "Up 5 minutes"}),
        ]
    )

    docker_stats_output = "\n".join(
        [
            json.dumps({"Name": None, "CPUPerc": "1.5%", "MemUsage": "50MB"}),
            json.dumps(
                {"Name": ["dockfleet_web"], "CPUPerc": "2.0%", "MemUsage": "60MB"}
            ),
            json.dumps({"OtherKey": "val"}),
        ]
    )

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


def test_system_status_counts_restarting_services(monkeypatch):
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc_restarting = DBService(
            name="web_restarting",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.RESTARTING,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=1,
        )
        svc_healthy = DBService(
            name="web_healthy",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        svc_unhealthy = DBService(
            name="web_unhealthy",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.UNHEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        svc_stopped = DBService(
            name="web_stopped",
            status=ContainerStatus.STOPPED,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        session.add_all([svc_restarting, svc_healthy, svc_unhealthy, svc_stopped])
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )

    docker_ps_output = "\n".join(
        [
            json.dumps({"Names": "dockfleet_web_restarting", "Status": "Up 10 seconds", "RunningFor": "10 seconds"}),
            json.dumps({"Names": "dockfleet_web_healthy", "Status": "Up 10 minutes", "RunningFor": "10 minutes"}),
            json.dumps({"Names": "dockfleet_web_unhealthy", "Status": "Up 5 minutes", "RunningFor": "5 minutes"}),
            json.dumps({"Names": "dockfleet_web_stopped", "Status": "Exited (0) 5 minutes ago", "RunningFor": "5 minutes"}),
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
        # 1. Direct function call
        res = system_status()
        assert res["total_services"] == 4
        assert res["restarting"] == 1
        assert res["running"] == 1
        assert res["unhealthy"] == 1
        assert res["stopped"] == 1
        assert res["running"] + res["restarting"] + res["stopped"] + res["unhealthy"] == res["total_services"]

        # 2. HTTP GET /status endpoint call
        async def _test_http():
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                return await client.get("/status")

        response = asyncio.run(_test_http())
        assert response.status_code == 200
        data = response.json()
        assert data["total_services"] == 4
        assert data["restarting"] == 1
        assert data["running"] == 1
        assert data["unhealthy"] == 1
        assert data["stopped"] == 1
        assert data["running"] + data["restarting"] + data["stopped"] + data["unhealthy"] == data["total_services"]


def test_metrics_calculates_stopped_and_running_from_container_status(monkeypatch, tmp_path):
    """
    Verify that GET /metrics calculates running_services from container lifecycle status
    (status == 'running') and stopped_services (status == 'stopped'), rather than health_status.
    """
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        # Service 1: running container, healthy
        svc_healthy = DBService(
            name="web_healthy",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        # Service 2: running container, unhealthy (still an active running container)
        svc_unhealthy = DBService(
            name="web_unhealthy",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.UNHEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=2,
        )
        # Service 3: stopped container
        svc_stopped = DBService(
            name="web_stopped",
            status=ContainerStatus.STOPPED,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=1,
        )
        session.add_all([svc_healthy, svc_unhealthy, svc_stopped])
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )
    monkeypatch.setattr(
        "dockfleet.dashboard.routes.get_session",
        lambda: get_session(engine=test_engine),
    )

    docker_ps_output = "\n".join(
        [
            json.dumps({"Names": "dockfleet_web_healthy", "Status": "Up 10 minutes", "RunningFor": "10 minutes"}),
            json.dumps({"Names": "dockfleet_web_unhealthy", "Status": "Up 5 minutes", "RunningFor": "5 minutes"}),
            json.dumps({"Names": "dockfleet_web_stopped", "Status": "Exited (0) 5 minutes ago", "RunningFor": "5 minutes"}),
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
        # 1. Direct function call
        metrics = get_metrics()
        assert metrics.total_services == 3
        assert metrics.running_services == 2  # web_healthy and web_unhealthy are both running
        assert metrics.unhealthy_services == 1  # web_unhealthy is failing health checks
        assert metrics.stopped_services == 1  # web_stopped is stopped
        assert metrics.total_restarts == 3

        # 2. HTTP GET /metrics endpoint call
        async def _test_http():
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                return await client.get("/metrics")

        response = asyncio.run(_test_http())
        assert response.status_code == 200
        data = response.json()
        assert data["total_services"] == 3
        assert data["running_services"] == 2
        assert data["unhealthy_services"] == 1
        assert data["stopped_services"] == 1
        assert data["total_restarts"] == 3


def test_get_services_preserves_unhealthy_status_for_stopped_containers(monkeypatch):
    """Verify that get_services and /services preserve UNHEALTHY / CRASHED states for stopped containers."""
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc_unhealthy_stopped = DBService(
            name="svc_unhealthy_stopped",
            status=ContainerStatus.STOPPED,
            health_status=HealthStatus.UNHEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=3,
        )
        svc_crashed_stopped = DBService(
            name="svc_crashed_stopped",
            status=ContainerStatus.STOPPED,
            health_status=HealthStatus.CRASHED,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=5,
        )
        svc_healthy_stopped = DBService(
            name="svc_healthy_stopped",
            status=ContainerStatus.STOPPED,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
        )
        session.add_all([svc_unhealthy_stopped, svc_crashed_stopped, svc_healthy_stopped])
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )
    monkeypatch.setattr(
        "dockfleet.dashboard.routes.get_session",
        lambda: get_session(engine=test_engine),
    )

    docker_ps_output = "\n".join(
        [
            json.dumps({"Names": "dockfleet_svc_unhealthy_stopped", "Status": "Exited (1) 2 minutes ago"}),
            json.dumps({"Names": "dockfleet_svc_crashed_stopped", "Status": "Exited (137) 1 minute ago"}),
            json.dumps({"Names": "dockfleet_svc_healthy_stopped", "Status": "Exited (0) 10 minutes ago"}),
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
        assert services_by_name["svc_unhealthy_stopped"]["status"] == ContainerStatus.STOPPED.value
        assert services_by_name["svc_unhealthy_stopped"]["health_status"] == HealthStatus.UNHEALTHY.value

        assert services_by_name["svc_crashed_stopped"]["status"] == ContainerStatus.STOPPED.value
        assert services_by_name["svc_crashed_stopped"]["health_status"] == HealthStatus.CRASHED.value

        assert services_by_name["svc_healthy_stopped"]["status"] == ContainerStatus.STOPPED.value
        assert services_by_name["svc_healthy_stopped"]["health_status"] == HealthStatus.HEALTHY.value

        # Test /services endpoint
        async def _test_http():
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                return await client.get("/services")

        response = asyncio.run(_test_http())
        assert response.status_code == 200
        data = {s["name"]: s for s in response.json()}
        assert data["svc_unhealthy_stopped"]["status"] == "stopped"
        assert data["svc_unhealthy_stopped"]["health_status"] == "unhealthy"
        assert data["svc_crashed_stopped"]["status"] == "stopped"
        assert data["svc_crashed_stopped"]["health_status"] == "crashed"
        assert data["svc_healthy_stopped"]["status"] == "stopped"
        assert data["svc_healthy_stopped"]["health_status"] == "healthy"


def test_get_services_docker_ps_restarting_status(monkeypatch):
    """
    Verify that when docker ps reports container status as 'Restarting (...)',
    container lifecycle status is ContainerStatus.RUNNING ('running') and
    health_status is HealthStatus.RESTARTING ('restarting').
    """
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc = DBService(
            name="web_restart_loop",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=3,
        )
        session.add(svc)
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )
    monkeypatch.setattr(
        "dockfleet.dashboard.routes.get_session",
        lambda: get_session(engine=test_engine),
    )

    docker_ps_output = json.dumps(
        {
            "Names": "dockfleet_web_restart_loop",
            "Status": "Restarting (1) 5 seconds ago",
            "RunningFor": "5 minutes",
        }
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
        assert len(services) == 1
        assert services[0]["name"] == "web_restart_loop"
        assert services[0]["status"] == ContainerStatus.RUNNING.value
        assert services[0]["health_status"] == HealthStatus.RESTARTING.value

        # Test /status route
        status = system_status()
        assert status["total_services"] == 1
        assert status["running"] == 0
        assert status["restarting"] == 1
        assert status["stopped"] == 0
        assert status["running"] + status["restarting"] + status["stopped"] == status["total_services"]


def test_dashboard_restart_endpoint_resets_consecutive_failures(monkeypatch, tmp_path):
    """
    Verify that calling POST /services/{name}/restart resets consecutive_failures to 0.
    """
    from dockfleet.health.status import update_service_health

    db_path = tmp_path / "test_manual_restart.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc = DBService(
            name="api",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.CRASHED,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=1,
            consecutive_failures=3,
        )
        session.add(svc)
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )
    monkeypatch.setattr(
        "dockfleet.dashboard.routes.get_session",
        lambda: get_session(engine=test_engine),
    )
    monkeypatch.setattr(
        "dockfleet.health.status.get_session",
        lambda: get_session(engine=test_engine),
    )

    mock_orch = MagicMock()
    mock_orch.restart_service.return_value = True
    monkeypatch.setattr("dockfleet.dashboard.routes.get_orchestrator", lambda: mock_orch)

    async def _test_http():
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post("/services/api/restart")

    response = asyncio.run(_test_http())
    assert response.status_code == 200
    assert response.json()["ok"] is True

    with Session(test_engine) as session:
        svc_after = session.exec(select(DBService).where(DBService.name == "api")).one()
        assert svc_after.consecutive_failures == 0
        assert svc_after.health_status == HealthStatus.HEALTHY
        assert svc_after.status == ContainerStatus.RUNNING

    # 1 subsequent failure
    update_service_health("api", is_healthy=False, reason="error 1")
    with Session(test_engine) as session:
        svc_next = session.exec(select(DBService).where(DBService.name == "api")).one()
        assert svc_next.consecutive_failures == 1
        assert svc_next.health_status == HealthStatus.UNHEALTHY



def test_get_services_matching_dockfleet_prefix_in_service_name(monkeypatch):
    """
    Verify that a service named 'dockfleet_worker' matches container 'dockfleet_dockfleet_worker'
    and properly displays its runtime stats (status, cpu, memory, uptime).
    """
    test_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc = DBService(
            name="dockfleet_worker",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
            image="worker:alpine",
            restart_policy="always",
            restart_count=0,
        )
        session.add(svc)
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )

    docker_ps_output = json.dumps(
        {
            "Names": "dockfleet_dockfleet_worker",
            "Status": "Up 12 minutes",
            "RunningFor": "12 minutes",
        }
    )

    docker_stats_output = json.dumps(
        {
            "Name": "dockfleet_dockfleet_worker",
            "CPUPerc": "3.5%",
            "MemUsage": "120MB",
        }
    )

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
    assert services[0]["name"] == "dockfleet_worker"
    assert services[0]["status"] == ContainerStatus.RUNNING.value
    assert services[0]["uptime"] == "12 minutes"
    assert services[0]["cpu"] == "3.5%"
    assert services[0]["memory"] == "120MB"


def test_manual_stop_service_uses_orchestrator(tmp_path, monkeypatch):
    """
    Verify that calling POST /services/{name}/stop calls Orchestrator.stop_service
    for proper container cleanup and marks service stopped in DB.
    """
    db_path = tmp_path / "test_manual_stop.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        svc = DBService(
            name="api",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
            consecutive_failures=0,
        )
        session.add(svc)
        session.commit()

    monkeypatch.setattr(
        "dockfleet.dashboard.services.get_session",
        lambda: get_session(engine=test_engine),
    )
    monkeypatch.setattr(
        "dockfleet.dashboard.routes.get_session",
        lambda: get_session(engine=test_engine),
    )
    monkeypatch.setattr(
        "dockfleet.health.status.get_session",
        lambda: get_session(engine=test_engine),
    )

    mock_orch = MagicMock()
    mock_orch.stop_service.return_value = True
    monkeypatch.setattr("dockfleet.dashboard.routes.get_orchestrator", lambda: mock_orch)

    async def _test_http():
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post("/services/api/stop")

    response = asyncio.run(_test_http())
    assert response.status_code == 200
    assert response.json()["ok"] is True
    mock_orch.stop_service.assert_called_once_with("api")

    with Session(test_engine) as session:
        svc_after = session.exec(select(DBService).where(DBService.name == "api")).one()
        assert svc_after.status == ContainerStatus.STOPPED
        assert svc_after.health_status == HealthStatus.HEALTHY






