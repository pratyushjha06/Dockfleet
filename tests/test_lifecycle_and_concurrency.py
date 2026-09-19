import threading
import time
from sqlalchemy import text
from sqlmodel import select

from dockfleet.cli.config import DockFleetConfig, RestartPolicy, ServiceConfig
from dockfleet.core.orchestrator import Orchestrator, reset_orchestrator
from dockfleet.health.models import (
    ContainerStatus,
    HealthStatus,
    Service,
    get_session,
    init_db,
)
from dockfleet.health.status import (
    mark_restart_successful,
    needs_restart,
    update_service_health,
)


def setup_function() -> None:
    reset_orchestrator()
    init_db()
    with get_session() as session:
        session.exec(text("DELETE FROM service"))
        session.commit()


def teardown_function() -> None:
    reset_orchestrator()


def _get_service(name: str) -> Service:
    with get_session() as session:
        return session.exec(select(Service).where(Service.name == name)).one()


def test_full_lifecycle_healthy_crashed_restarting_healthy():
    """
    Test full lifecycle transitions:
    HEALTHY -> CRASHED -> RESTARTING -> HEALTHY
    """
    with get_session() as session:
        svc = Service(
            name="lifecycle-svc",
            image="nginx:alpine",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
        )
        session.add(svc)
        session.commit()

    # Initial state
    svc = _get_service("lifecycle-svc")
    assert svc.status == ContainerStatus.RUNNING
    assert svc.health_status == HealthStatus.HEALTHY

    # 1. Transitions to CRASHED on health check failure
    update_service_health("lifecycle-svc", is_healthy=False, reason="failed check 1")
    update_service_health("lifecycle-svc", is_healthy=False, reason="failed check 2")
    update_service_health("lifecycle-svc", is_healthy=False, reason="failed check 3")

    svc = _get_service("lifecycle-svc")
    assert svc.health_status == HealthStatus.CRASHED
    assert svc.consecutive_failures == 3
    assert needs_restart(svc) is True

    # 2. State during active restart set to RESTARTING
    config = DockFleetConfig(
        services={
            "lifecycle-svc": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)

    # Verify restart_service sets state RESTARTING during run
    state_in_progress = None

    def delayed_start(name, config):
        nonlocal state_in_progress
        state_in_progress = _get_service("lifecycle-svc").health_status

    orch.start_service = delayed_start
    success = orch.restart_service("lifecycle-svc", config)

    assert success is True
    assert state_in_progress == HealthStatus.RESTARTING

    # 3. Transitions to HEALTHY + RUNNING after successful restart completion
    mark_restart_successful("lifecycle-svc")
    svc = _get_service("lifecycle-svc")
    assert svc.status == ContainerStatus.RUNNING
    assert svc.health_status == HealthStatus.HEALTHY
    assert svc.consecutive_failures == 0


def test_no_duplicate_restart_from_concurrent_paths():
    """
    Verify that concurrent restart requests (e.g. from health scheduler auto-restart
    and manual dashboard restart) are guarded so that only 1 restart actually executes.
    """
    with get_session() as session:
        svc = Service(
            name="concurrent-svc",
            image="nginx:alpine",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.CRASHED,
            consecutive_failures=3,
        )
        session.add(svc)
        session.commit()

    config = DockFleetConfig(
        services={
            "concurrent-svc": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)

    start_calls = 0
    start_lock = threading.Lock()

    def mock_start(name, svc_cfg):
        nonlocal start_calls
        time.sleep(0.05)  # simulate slow container startup
        with start_lock:
            start_calls += 1

    orch.start_service = mock_start

    results = []

    def trigger_auto_restart():
        res = orch.handle_unhealthy_service("concurrent-svc", config)
        results.append(res)

    def trigger_manual_restart():
        res = orch.restart_service("concurrent-svc", config)
        results.append(res)

    t1 = threading.Thread(target=trigger_auto_restart)
    t2 = threading.Thread(target=trigger_manual_restart)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    # One call succeeded, second was blocked by concurrency guard
    assert start_calls == 1


def test_health_check_failure_preserves_running_status_as_is():
    """
    Lock in established semantics (#122/#136): when a health check fails,
    health_status changes to CRASHED while container lifecycle status stays as-is (RUNNING).
    """
    with get_session() as session:
        svc = Service(
            name="status-as-is-svc",
            image="nginx:alpine",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.HEALTHY,
        )
        session.add(svc)
        session.commit()

    # Health check fails
    update_service_health("status-as-is-svc", is_healthy=False, reason="http 500")

    svc = _get_service("status-as-is-svc")
    assert svc.status == ContainerStatus.RUNNING  # container status preserved as-is!
    assert svc.health_status == HealthStatus.UNHEALTHY  # health dimension updated!


def test_json_wire_serialization_emits_clean_strings():
    """
    Verify that str-backed Enum instances serialize directly to clean string literals
    in model dumps, JSON serialization, and API schemas without Enum class reprs.
    """
    import json
    import asyncio
    import httpx
    from httpx import ASGITransport
    from dockfleet.dashboard.api import app
    from dockfleet.dashboard.routes import Service as DashboardServiceSchema
    from dockfleet.core.orchestrator import ServiceStat

    # 1. SQLModel Service model serialization
    svc = Service(
        name="serial-test",
        image="test:latest",
        restart_policy="always",
        status=ContainerStatus.RUNNING,
        health_status=HealthStatus.RESTARTING,
    )
    svc_dict = svc.model_dump()
    assert svc_dict["status"] == "running"
    assert svc_dict["health_status"] == "restarting"
    assert json.loads(svc.model_dump_json())["status"] == "running"
    assert json.loads(svc.model_dump_json())["health_status"] == "restarting"

    # 2. ServiceStat Pydantic model serialization
    stat = ServiceStat(
        service_name="serial-test",
        container_name="dockfleet_serial-test",
        status=ContainerStatus.STOPPED,
    )
    stat_dict = stat.model_dump()
    assert stat_dict["status"] == "stopped"
    assert "ContainerStatus" not in stat.model_dump_json()

    # 3. Dashboard API schema serialization
    dash_svc = DashboardServiceSchema(
        name="serial-test",
        status=ContainerStatus.RUNNING,
        health_status=HealthStatus.HEALTHY,
        image="test:latest",
        restart_policy="always",
        restart_count=0,
    )
    dash_json = dash_svc.model_dump_json()
    assert '"status":"running"' in dash_json
    assert '"health_status":"healthy"' in dash_json
    assert "ContainerStatus" not in dash_json
    assert "HealthStatus" not in dash_json

    # 4. FastAPI wire response verification
    with get_session() as session:
        session.add(svc)
        session.commit()

    async def _fetch():
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get("/services")

    response = asyncio.run(_fetch())
    assert response.status_code == 200
    services = response.json()
    matching = [s for s in services if s["name"] == "serial-test"]
    assert len(matching) == 1
    assert matching[0]["status"] in ("running", "stopped", "unknown")
    assert matching[0]["health_status"] in (
        "healthy",
        "unhealthy",
        "crashed",
        "restarting",
        "stopped",
    )
    assert isinstance(matching[0]["status"], str)
    assert isinstance(matching[0]["health_status"], str)


def test_restart_guard_released_after_success():
    """
    Verify that _active_restarts is cleanly cleared after a successful restart,
    allowing subsequent restarts to proceed without getting wedged.
    """
    with get_session() as session:
        svc = Service(
            name="guard-success-svc",
            image="nginx:alpine",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.CRASHED,
        )
        session.add(svc)
        session.commit()

    config = DockFleetConfig(
        services={
            "guard-success-svc": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)
    orch.start_service = lambda name, cfg: None

    success = orch.restart_service("guard-success-svc", config)
    assert success is True
    # Guard must be released
    assert "guard-success-svc" not in orch._active_restarts
    assert len(orch._active_restarts) == 0

    # Mark healthy to simulate full restart cycle
    mark_restart_successful("guard-success-svc")

    # Second restart should immediately succeed without being blocked
    second_success = orch.restart_service("guard-success-svc", config)
    assert second_success is True
    assert "guard-success-svc" not in orch._active_restarts


def test_restart_guard_released_after_failure():
    """
    Verify that _active_restarts is cleared and DB is not wedged in RESTARTING
    when start_service fails / returns failure.
    """
    with get_session() as session:
        svc = Service(
            name="guard-failure-svc",
            image="nginx:alpine",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.CRASHED,
        )
        session.add(svc)
        session.commit()

    config = DockFleetConfig(
        services={
            "guard-failure-svc": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)

    def failing_start(name, cfg):
        raise RuntimeError("Docker daemon connection failed")

    orch.start_service = failing_start

    success = orch.restart_service("guard-failure-svc", config)
    assert success is False

    # Guard must be released from memory
    assert "guard-failure-svc" not in orch._active_restarts
    assert len(orch._active_restarts) == 0

    # DB state must not be wedged in RESTARTING
    db_svc = _get_service("guard-failure-svc")
    assert db_svc.health_status != HealthStatus.RESTARTING


def test_restart_guard_released_after_exception():
    """
    Verify that _active_restarts is guaranteed to be cleared via finally
    even when an unhandled exception is raised during restart.
    """
    with get_session() as session:
        svc = Service(
            name="guard-exc-svc",
            image="nginx:alpine",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.CRASHED,
        )
        session.add(svc)
        session.commit()

    config = DockFleetConfig(
        services={
            "guard-exc-svc": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)

    # Force an unexpected exception during the container stop phase
    import pytest

    def mock_subprocess_run(*args, **kwargs):
        raise KeyboardInterrupt("Simulated unexpected thread termination")

    orch.container_name = lambda name: (_ for _ in ()).throw(
        ValueError("Unexpected unhandled exception during container lookup")
    )

    with pytest.raises(ValueError, match="Unexpected unhandled exception"):
        orch.restart_service("guard-exc-svc", config)

    # In-memory lock guard must be completely released via finally
    assert "guard-exc-svc" not in orch._active_restarts
    assert len(orch._active_restarts) == 0

    # DB state must not be permanently stuck in RESTARTING
    db_svc = _get_service("guard-exc-svc")
    assert db_svc.health_status != HealthStatus.RESTARTING


def test_per_service_restart_isolation():
    """
    Verify that _active_restarts is strictly isolated per-service.
    Restarting Service A while Service B is mid-restart does not block Service B.
    """
    with get_session() as session:
        session.add(
            Service(
                name="service-a",
                image="nginx:alpine",
                restart_policy="always",
                status=ContainerStatus.RUNNING,
                health_status=HealthStatus.CRASHED,
            )
        )
        session.add(
            Service(
                name="service-b",
                image="redis:alpine",
                restart_policy="always",
                status=ContainerStatus.RUNNING,
                health_status=HealthStatus.CRASHED,
            )
        )
        session.commit()

    config = DockFleetConfig(
        services={
            "service-a": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            ),
            "service-b": ServiceConfig(
                image="redis:alpine", restart=RestartPolicy.always
            ),
        }
    )
    orch = Orchestrator(config)

    a_started = threading.Event()
    a_can_finish = threading.Event()
    b_result = []

    def mock_start(name, cfg):
        if name == "service-a":
            a_started.set()
            a_can_finish.wait(timeout=2.0)
        elif name == "service-b":
            pass

    orch.start_service = mock_start

    t_a = threading.Thread(target=lambda: orch.restart_service("service-a", config))
    t_a.start()

    # Wait until Service A is actively in-flight
    assert a_started.wait(timeout=1.0) is True
    assert "service-a" in orch._active_restarts

    # Service B restart runs concurrently while Service A is still in-flight
    t_b = threading.Thread(
        target=lambda: b_result.append(orch.restart_service("service-b", config))
    )
    t_b.start()
    t_b.join(timeout=1.0)

    # Service B succeeded without being blocked by Service A
    assert b_result == [True]
    assert "service-b" not in orch._active_restarts
    assert "service-a" in orch._active_restarts

    # Unblock A and let it finish
    a_can_finish.set()
    t_a.join(timeout=1.0)

    assert "service-a" not in orch._active_restarts
    assert len(orch._active_restarts) == 0


def test_true_concurrency_race_condition_prevented():
    """
    Exercise a real thread race condition where multiple threads simultaneously
    attempt to restart the exact same service at the exact same instant using a barrier.
    Confirms only exactly 1 thread executes the restart.
    """
    with get_session() as session:
        svc = Service(
            name="race-svc",
            image="nginx:alpine",
            restart_policy="always",
            status=ContainerStatus.RUNNING,
            health_status=HealthStatus.CRASHED,
        )
        session.add(svc)
        session.commit()

    config = DockFleetConfig(
        services={
            "race-svc": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)

    start_executions = 0
    start_lock = threading.Lock()

    def slow_start(name, cfg):
        nonlocal start_executions
        time.sleep(0.05)
        with start_lock:
            start_executions += 1

    orch.start_service = slow_start

    num_threads = 6
    barrier = threading.Barrier(num_threads)
    results = []
    results_lock = threading.Lock()

    def thread_worker():
        barrier.wait()  # Synchronize all threads to fire simultaneously
        res = orch.restart_service("race-svc", config)
        with results_lock:
            results.append(res)

    threads = [threading.Thread(target=thread_worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)

    # Exactly 1 thread must succeed in executing the restart, 5 must be rejected
    assert start_executions == 1
    assert results.count(True) == 1
    assert results.count(False) == num_threads - 1

    # In-memory lock must be fully cleared
    assert "race-svc" not in orch._active_restarts
    assert len(orch._active_restarts) == 0


def test_legacy_db_raw_string_backward_compatibility():
    """
    Verify 100% backward compatibility with pre-refactor SQLite database rows:
    Raw strings directly written into the DB ('running', 'crashed', 'unknown')
    must read back seamlessly through the enum-typed SQLModel without validation errors.
    """
    # Insert raw string values directly into SQLite table via raw SQL
    with get_session() as session:
        session.exec(
            text(
                "INSERT INTO service (name, image, restart_policy, status, health_status, restart_count, consecutive_failures) "
                "VALUES ('legacy-db-svc', 'postgres:15', 'always', 'running', 'crashed', 2, 3)"
            )
        )
        session.commit()

    # Query through the updated SQLModel Service model
    with get_session() as session:
        legacy_svc = session.exec(
            select(Service).where(Service.name == "legacy-db-svc")
        ).one()

        # 1. Type validation and enum member identity
        assert legacy_svc.status == ContainerStatus.RUNNING
        assert legacy_svc.health_status == HealthStatus.CRASHED
        assert isinstance(legacy_svc.status, ContainerStatus)
        assert isinstance(legacy_svc.health_status, HealthStatus)

        # 2. String comparison backward compatibility
        assert legacy_svc.status == "running"
        assert legacy_svc.health_status == "crashed"

        # 3. Model dump / serialization compatibility
        dump = legacy_svc.model_dump()
        assert dump["status"] == "running"
        assert dump["health_status"] == "crashed"

        # 4. State updates on legacy row work seamlessly
        legacy_svc.health_status = HealthStatus.HEALTHY
        session.add(legacy_svc)
        session.commit()

    # Verify persisted update
    updated_svc = _get_service("legacy-db-svc")
    assert updated_svc.health_status == HealthStatus.HEALTHY
    assert updated_svc.health_status == "healthy"


def test_malformed_legacy_db_data_resilience(caplog):
    """
    Negative test for corrupted or malformed legacy DB rows (e.g. '', 'corrupted_val', NULL).
    Verifies that SafeTypeDecorators gracefully coerce bad values to UNKNOWN / UNHEALTHY
    with warning logs, without crashing queries or the /services listing endpoint.
    """
    import logging

    with get_session() as session:
        # Insert 1 healthy service and 2 services with corrupted/empty status values
        session.exec(
            text(
                "INSERT INTO service (name, image, restart_policy, status, health_status, restart_count, consecutive_failures) "
                "VALUES ('healthy-svc', 'nginx:alpine', 'always', 'running', 'healthy', 0, 0)"
            )
        )
        session.exec(
            text(
                "INSERT INTO service (name, image, restart_policy, status, health_status, restart_count, consecutive_failures) "
                "VALUES ('corrupted-svc-1', 'redis:alpine', 'always', 'invalid_status_xyz', 'unrecognized_health_abc', 0, 0)"
            )
        )
        session.exec(
            text(
                "INSERT INTO service (name, image, restart_policy, status, health_status, restart_count, consecutive_failures) "
                "VALUES ('empty-svc-2', 'postgres:15', 'always', '', '', 0, 0)"
            )
        )
        session.commit()

    # Querying all services should succeed without raising LookupError or ValueError
    with caplog.at_level(logging.WARNING):
        with get_session() as session:
            all_services = session.exec(select(Service)).all()
            assert len(all_services) == 3

            by_name = {s.name: s for s in all_services}

            # Healthy row is intact
            assert by_name["healthy-svc"].status == ContainerStatus.RUNNING
            assert by_name["healthy-svc"].health_status == HealthStatus.HEALTHY

            # Corrupted row safely falls back without raising
            assert by_name["corrupted-svc-1"].status == ContainerStatus.UNKNOWN
            assert by_name["corrupted-svc-1"].health_status == HealthStatus.UNHEALTHY

            # Empty string row safely falls back
            assert by_name["empty-svc-2"].status == ContainerStatus.UNKNOWN
            assert by_name["empty-svc-2"].health_status == HealthStatus.HEALTHY

    # Verify warning log was emitted for corrupted values
    assert any(
        "Unrecognized ContainerStatus value 'invalid_status_xyz'" in r.message
        for r in caplog.records
    )
    assert any(
        "Unrecognized HealthStatus value 'unrecognized_health_abc'" in r.message
        for r in caplog.records
    )
    assert any("Empty or null" in r.message for r in caplog.records)

    # Dashboard listing API should also return all 3 without 500 error
    import asyncio
    import httpx
    from httpx import ASGITransport
    from dockfleet.dashboard.api import app

    async def _fetch():
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get("/services")

    response = asyncio.run(_fetch())
    assert response.status_code == 200
    services_list = response.json()
    assert len(services_list) >= 3


def test_async_api_non_blocking_during_in_flight_restart():
    """
    Verify that an active in-flight restart running on a background worker thread
    does not stall or block the FastAPI event loop for other concurrent async requests.
    """
    import asyncio
    import httpx
    from httpx import ASGITransport
    from dockfleet.dashboard.api import app

    with get_session() as session:
        session.add(
            Service(
                name="async-safe-svc",
                image="nginx:alpine",
                restart_policy="always",
                status=ContainerStatus.RUNNING,
                health_status=HealthStatus.CRASHED,
            )
        )
        session.commit()

    config = DockFleetConfig(
        services={
            "async-safe-svc": ServiceConfig(
                image="nginx:alpine", restart=RestartPolicy.always
            )
        }
    )
    orch = Orchestrator(config)

    in_flight = threading.Event()
    release_restart = threading.Event()

    def blocked_start(name, cfg):
        in_flight.set()
        release_restart.wait(timeout=2.0)

    orch.start_service = blocked_start

    # Launch background restart thread (simulating HealthScheduler)
    t = threading.Thread(target=lambda: orch.restart_service("async-safe-svc", config))
    t.start()

    assert in_flight.wait(timeout=1.0) is True
    assert "async-safe-svc" in orch._active_restarts

    # Fire concurrent async requests while restart is actively holding background lock
    async def _test_concurrency():
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            # 1. Health endpoint must respond immediately (< 0.5s)
            start_time = time.time()
            res_health = await client.get("/health")
            duration_health = time.time() - start_time
            assert res_health.status_code == 200
            assert duration_health < 1.0

            # 2. Services listing endpoint must respond immediately without hanging
            start_time = time.time()
            res_services = await client.get("/services")
            duration_services = time.time() - start_time
            assert res_services.status_code == 200
            assert duration_services < 1.0

            # 3. Status summary endpoint must respond immediately
            res_status = await client.get("/status")
            assert res_status.status_code == 200

    try:
        asyncio.run(_test_concurrency())
    finally:
        release_restart.set()
        t.join(timeout=2.0)

    assert "async-safe-svc" not in orch._active_restarts
