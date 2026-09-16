import pytest
from unittest.mock import MagicMock
from sqlmodel import Session, select
from sqlalchemy import text
from dockfleet.cli.config import DockFleetConfig, ServiceConfig, RestartPolicy
from dockfleet.health.models import Service, init_db, engine
from dockfleet.health.status import update_service_health, needs_restart
from dockfleet.core.orchestrator import Orchestrator 

def setup_function():
    """Per-test reset."""
    init_db()
    with Session(engine) as session:
        session.exec(text("DELETE FROM service")) 
        session.commit()

def _get_service(name: str) -> Service:
    """Helper to fetch service from DB."""
    with Session(engine) as session:
        return session.exec(
            select(Service).where(Service.name == name)
        ).one()
    
def test_restart_service_happy_path():
    """Test orchestrator restart path."""
    # Real config with service
    config = DockFleetConfig(
        services={
            "svc-orch": ServiceConfig(
                image="nginx:alpine",
                restart=RestartPolicy.always,
                ports=["8080:80"]
            )
        }
    )
    
    orch = Orchestrator(config)  # Instance
        # Mock Docker calls so this test verifies orchestrator logic,
    # not a real local Docker daemon.
    orch.docker.run_container = MagicMock()
    orch.docker.stop_container = MagicMock()
    
    # Create DB service
    with Session(engine) as session:
        svc = Service(name="svc-orch", image="nginx:alpine", restart_policy="always")
        session.add(svc)
        session.commit()
    
    # Simulate failures
    update_service_health("svc-orch", False, "fail 1")
    update_service_health("svc-orch", False, "fail 2") 
    update_service_health("svc-orch", False, "fail 3")
    
    assert needs_restart(_get_service("svc-orch"))
    
    # Call INSTANCE method
    orch.restart_service("svc-orch", config)
    
    # Verify DB restart_count incremented
    svc = _get_service("svc-orch")
    assert svc.restart_count >= 1

def test_restart_failure_marks_crashed():
    """Test failure handling."""
    # CREATE DB SERVICE FIRST (missing step!)
    with Session(engine) as session:
        svc = Service(
            name="svc-fail", 
            image="fail-image", 
            restart_policy="always",
            status="running"
        )
        session.add(svc)
        session.commit()
    
    config = DockFleetConfig(
        services={
            "svc-fail": ServiceConfig(image="fail-image", restart=RestartPolicy.always)
        }
    )
    orch = Orchestrator(config)
    
    # Simulate 3 failures → triggers restart
    update_service_health("svc-fail", False, "fail 1")
    update_service_health("svc-fail", False, "fail 2")
    update_service_health("svc-fail", False, "fail 3")
    
    # This will FAIL restart → trigger _mark_restart_failed()
    orch.handle_unhealthy_service("svc-fail", config, "test failure")
    
    # ✅ Now service exists + marked crashed
    svc = _get_service("svc-fail")
    assert svc.status == "stopped"
    assert svc.health_status == "crashed"
    assert "auto-restart failed" in (svc.last_failure_reason or "")