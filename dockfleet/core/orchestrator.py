from dockfleet.core.docker import DockerManager
from dockfleet.health.status import (
    mark_service_running,
    mark_service_stopped,
    mark_restart_successful,
    record_restart_event
)

from dockfleet.health.models import Service, engine
from dockfleet.health.seed import bootstrap_from_config
import logging
from sqlmodel import Session, select
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class Orchestrator:

    def __init__(self, config):
        self.config = config
        self.docker = DockerManager()
        self.network = "dockfleet_net"

    def container_name(self, service):
        return f"dockfleet_{service}"

    def start_service(self, name, svc):

        container_name = self.container_name(name)
        ports = svc.ports or []

        try:

            self.docker.remove_container(container_name)

            self.docker.run_container(
                image=svc.image,
                name=container_name,
                ports=ports,
                network=self.network
            )

            mark_service_running(name)

            print(f"Started service: {name}")

        except Exception as e:

            print(f"Failed to start {name}")
            print(e)

    def stop_service(self, name):

        container_name = self.container_name(name)

        try:

            self.docker.stop_container(container_name)
            self.docker.remove_container(container_name)

            mark_service_stopped(name)

            print(f"Stopped service: {name}")

        except Exception as e:

            print(f"Failed to stop {name}")
            print(e)

    def restart_service(self, service_name: str, config=None) -> bool:
        """Day 11: COMPLETE idempotent restart + DB restart_count."""
        config = config or self.config
        
        if service_name not in config.services:
            logger.warning(f"Service {service_name} not found")
            return False
        
        # ✅ IDEMPOTENT: Skip if not running
        import subprocess
        container_name = self.container_name(service_name)
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5
            )
            if not result.stdout.strip():
                logger.info(f"{service_name} not running, skipping restart")
                return False
        except:
            logger.warning(f"Cannot check {service_name}, skipping")
            return False
        
        svc = config.services[service_name]
        print(f"🔄 Restarting container: {service_name}")
        
        try:
            # Stop container
            self.stop_service(service_name)
            
            # ✅ DAY 11: SELF-CONTAINED DB restart_count increment
            self._increment_restart_count(service_name)
            
            # Start fresh
            self.start_service(service_name, svc)
            logger.info(f"✅ {service_name} restarted (restart_count incremented)")
            return True
            
        except Exception as e:
            logger.error(f"Container restart failed: {e}")
            return False

    def _increment_restart_count(self, service_name: str) -> None:
        """Self-contained DB restart_count increment (Day 11 req)."""
        try:
            with Session(engine) as session:
                svc = session.exec(
                    select(Service).where(Service.name == service_name)
                ).one_or_none()
                
                if svc:
                    # Increment restart_count (create if missing)
                    svc.restart_count = (svc.restart_count or 0) + 1
                    session.add(svc)
                    session.commit()
                    logger.info(f"DB: {service_name} restart_count={svc.restart_count}")
                else:
                    logger.warning(f"Service {service_name} not in DB")
                    
        except Exception as e:
            logger.error(f"DB increment failed for {service_name}: {e}")

    def handle_unhealthy_service(self, service_name: str, config=None, reason: str = "health failure") -> None:
        
        config = config or self.config  
        logger.info("Auto-restart: %s (%s)", service_name, reason)

        try:
            success = self.restart_service(service_name)
        except Exception as exc:
            logger.error("CRITICAL restart error %s: %s", service_name, exc)
            self._mark_restart_failed(service_name, str(exc))
            return
        
        if not success:
            logger.error("restart_service failed %s", service_name)
            self._mark_restart_failed(service_name, "restart_service returned False")
            return
        
        # SUCCESS 
        logger.info("%s auto-restarted", service_name)
        mark_restart_successful(service_name)
        
        # Record event
        with Session(engine) as session:
            svc = session.exec(select(Service).where(Service.name == service_name)).one_or_none()
            if svc:
                record_restart_event(svc, reason)
            else:
                logger.warning("Service %s not found after restart", service_name)

    def _mark_restart_failed(self, service_name: str, reason: str) -> None:

        with Session(engine) as session:
            svc = session.exec(select(Service).where(Service.name == service_name)).one_or_none()
            if svc:
                svc.status = "crashed"
                svc.last_failure_reason = f"auto-restart failed: {reason}"
                session.add(svc)
                session.commit()
                logger.error(" %s marked CRASHED: %s", service_name, reason)

    def up(self):
        print("Starting services...\n")
        
        bootstrap_from_config(self.config)
        print(" DB bootstrapped & services seeded")
       
        self.docker.create_network(self.network)
        for name, svc in self.config.services.items():
            self.start_service(name, svc)

    def down(self):

        print("Stopping services...\n")

        for name in self.config.services.keys():
            self.stop_service(name)

    def ps(self):

        print("Running containers:\n")

        self.docker.list_containers()