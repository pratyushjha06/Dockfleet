"""Regression tests for the bounded process health check.

A blocked ``docker inspect`` used to hang ``HealthChecker.check_process``
forever, which in turn froze the scheduler's polling loop.  These tests pin
the timeout behavior and prove that one stuck check no longer prevents other
services from being checked and recorded.
"""

import subprocess
from unittest.mock import patch

from sqlmodel import select

from dockfleet.cli.config import (
    DockFleetConfig,
    HealthCheckConfig,
    RestartPolicy,
    ServiceConfig,
)
from dockfleet.health.checker import HealthChecker
from dockfleet.health.models import ContainerStatus, Service, get_session, init_db
from dockfleet.health.scheduler import HealthScheduler
from dockfleet.health.services import seed_services


class TestCheckProcessTimeout:
    """docker inspect must be bounded so a stuck CLI cannot stall the scheduler."""

    @staticmethod
    def _completed(returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["docker", "inspect"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def test_running_container_returns_true_with_bounded_timeout(self):
        checker = HealthChecker()

        with patch(
            "dockfleet.health.checker.subprocess.run",
            return_value=self._completed(stdout="true\n"),
        ) as run:
            assert checker.check_process("dockfleet_api") is True

        assert run.call_args.kwargs["timeout"] == 5.0

    def test_inspect_timeout_is_reported_unhealthy(self):
        checker = HealthChecker()

        with patch(
            "dockfleet.health.checker.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["docker", "inspect"], timeout=5.0
            ),
        ):
            assert checker.check_process("dockfleet_api") is False

    def test_custom_timeout_is_forwarded(self):
        checker = HealthChecker()

        with patch(
            "dockfleet.health.checker.subprocess.run",
            return_value=self._completed(stdout="false\n"),
        ) as run:
            assert checker.check_process("dockfleet_api", timeout=0.5) is False

        assert run.call_args.kwargs["timeout"] == 0.5

    def test_missing_docker_cli_is_reported_unhealthy(self):
        checker = HealthChecker()

        with patch(
            "dockfleet.health.checker.subprocess.run", side_effect=FileNotFoundError()
        ):
            assert checker.check_process("dockfleet_api") is False


def test_scheduler_pass_completes_when_one_process_check_times_out():
    """A timed-out check must not block the pass: the healthy service is still recorded."""
    init_db()
    config = DockFleetConfig(
        services={
            "api": ServiceConfig(
                image="nginx",
                restart=RestartPolicy.always,
                healthcheck=HealthCheckConfig(type="process", interval=30),
            ),
            "web": ServiceConfig(
                image="nginx",
                restart=RestartPolicy.always,
                healthcheck=HealthCheckConfig(type="process", interval=30),
            ),
        }
    )

    with get_session() as session:
        seed_services(config, session)
        for svc in session.exec(select(Service)).all():
            svc.status = ContainerStatus.RUNNING
            session.add(svc)
        session.commit()

    def fake_run(cmd, **kwargs):
        if cmd[-1] == "dockfleet_api":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 5.0))
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="true\n", stderr=""
        )

    scheduler = HealthScheduler(config=config, interval_seconds=1)

    with patch("dockfleet.health.checker.subprocess.run", side_effect=fake_run):
        results = scheduler.run_single_pass()

    assert results == {"api": False, "web": True}

    with get_session() as session:
        api = session.exec(select(Service).where(Service.name == "api")).one()
        web = session.exec(select(Service).where(Service.name == "web")).one()

    assert api.consecutive_failures == 1
    assert web.health_status == "healthy"
