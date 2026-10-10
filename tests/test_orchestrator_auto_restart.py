from unittest.mock import MagicMock, patch
from sqlalchemy import text
from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, RestartPolicy, ServiceConfig
from dockfleet.core.orchestrator import Orchestrator
from dockfleet.health.models import (
    ContainerStatus,
    HealthStatus,
    Service,
    get_session,
    init_db,
)
from dockfleet.health.status import needs_restart, update_service_health


def setup_function():
    """Per-test reset."""
    init_db()
    with get_session() as session:
        session.exec(text("DELETE FROM service"))
        session.commit()


def _get_service(name: str) -> Service:
    """Helper to fetch service from DB."""
    with get_session() as session:
        return session.exec(select(Service).where(Service.name == name)).one()


def test_restart_service_happy_path():
    """Test orchestrator restart path."""
    config = DockFleetConfig(
        services={
            "svc-orch": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always, ports=["8080:80"]
            )
        }
    )

    orch = Orchestrator(config)

    with get_session() as session:
        svc = Service(name="svc-orch", image="nginx:alpine", restart_policy="always")
        session.add(svc)
        session.commit()

    update_service_health("svc-orch", False, "fail 1")
    update_service_health("svc-orch", False, "fail 2")
    update_service_health("svc-orch", False, "fail 3")

    assert needs_restart(_get_service("svc-orch"))

    with patch("subprocess.run") as mock_run, patch.object(orch, "start_service"):
        mock_run.return_value = MagicMock(returncode=0)
        orch.restart_service("svc-orch", config)

    svc = _get_service("svc-orch")
    assert svc.restart_count == 1


def test_auto_restart_increments_restart_count_exactly_once():
    """Test that auto-restart via handle_unhealthy_service increments restart_count exactly once."""
    config = DockFleetConfig(
        services={
            "svc-auto": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always, ports=["8080:80"]
            )
        }
    )

    orch = Orchestrator(config)

    with get_session() as session:
        svc = Service(
            name="svc-auto",
            image="nginx:alpine",
            restart_policy="always",
            restart_count=0,
            status=ContainerStatus.RUNNING,
        )
        session.add(svc)
        session.commit()

    update_service_health("svc-auto", False, "fail 1")
    update_service_health("svc-auto", False, "fail 2")
    update_service_health("svc-auto", False, "fail 3")

    with patch("subprocess.run") as mock_run, patch.object(orch, "start_service"):
        mock_run.return_value = MagicMock(returncode=0)
        orch.handle_unhealthy_service("svc-auto", config, "3_failed_health_checks")

    svc = _get_service("svc-auto")
    assert svc.restart_count == 1
    assert svc.consecutive_failures == 0
    assert svc.health_status == HealthStatus.HEALTHY



def test_restart_failure_marks_crashed():
    """Test failure handling when restart start_service raises an exception."""
    with get_session() as session:
        svc = Service(
            name="svc-fail",
            image="fail-image",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
        )
        session.add(svc)
        session.commit()

    config = DockFleetConfig(
        services={
            "svc-fail": ServiceConfig(image="fail-image", restart=RestartPolicy.always)
        }
    )
    orch = Orchestrator(config)

    update_service_health("svc-fail", False, "fail 1")
    update_service_health("svc-fail", False, "fail 2")
    update_service_health("svc-fail", False, "fail 3")

    with (
        patch("subprocess.run") as mock_run,
        patch.object(
            orch, "start_service", side_effect=RuntimeError("Docker engine down")
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        orch.handle_unhealthy_service("svc-fail", config, "test failure")

    svc = _get_service("svc-fail")
    assert svc.status == ContainerStatus.STOPPED
    assert svc.health_status == HealthStatus.CRASHED
    assert "auto-restart failed" in (svc.last_failure_reason or "")


def test_restart_service_executes_immediately_without_blocking():
    """Verify restart_service does not block or call time.sleep even if backoff_attempt > 0."""
    config = DockFleetConfig(
        services={
            "svc-noblock": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)

    with get_session() as session:
        svc = Service(
            name="svc-noblock", image="nginx:alpine", restart_policy="always"
        )
        session.add(svc)
        session.commit()

    with (
        patch("subprocess.run") as mock_run,
        patch.object(orch, "start_service", return_value=True),
        patch("time.sleep") as mock_sleep,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = orch.restart_service("svc-noblock", config, backoff_attempt=3)

    assert result is True
    mock_sleep.assert_not_called()
