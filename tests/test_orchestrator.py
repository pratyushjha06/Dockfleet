import subprocess
import threading
from unittest.mock import MagicMock, Mock, patch

import pytest

from dockfleet.cli.config import DockFleetConfig, RestartPolicy, ServiceConfig
from dockfleet.core.orchestrator import (
    Orchestrator,
    ServiceStat,
    get_container_name,
    get_orchestrator,
    get_service_stats,
    reset_orchestrator,
)
from dockfleet.health.models import ContainerStatus

# ------------------------------------------------
# Existing tests (cleaned up)
# ------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """Ensure every test starts and ends with no singleton."""
    reset_orchestrator()
    yield
    reset_orchestrator()


@pytest.fixture
def mock_config():
    """Mock config."""
    config = Mock()
    config.services = {
        "api": Mock(image="nginx", ports=[80]),
        "web": Mock(image="nginx", ports=[8080]),
    }
    return config


@pytest.fixture
def orchestrator(mock_config):
    """Mocked orchestrator."""
    orch = Mock(spec=Orchestrator)
    orch.config = mock_config
    orch.container_name = Mock(side_effect=lambda name: f"dockfleet_{name}")
    orch.docker = Mock()
    orch.logger = Mock()
    return orch


def test_get_container_name():
    """Basic container naming."""
    assert get_container_name("api") == "dockfleet_api"
    assert get_container_name("web") == "dockfleet_web"


@patch("subprocess.run")
def test_docker_ps_check(mock_run):
    """Test container existence check."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="dockfleet_api\n",
    )

    result = mock_run.return_value.stdout.strip()
    assert "dockfleet_api" in result


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_get_service_stats_wrapper(mock_orch_class):
    """Module wrapper works."""
    mock_orch = mock_orch_class.return_value
    mock_orch.get_service_stats.return_value = [
        ServiceStat(
            service_name="api", container_name="dockfleet_api", status="running"
        )
    ]

    stats = get_service_stats()

    assert len(stats) == 1
    mock_orch.get_service_stats.assert_called_once()


@pytest.mark.parametrize("service_name", ["api", "web", "db"])
def test_container_naming_consistent(service_name):
    """All services use dockfleet_ prefix."""
    name = get_container_name(service_name)
    assert name == f"dockfleet_{service_name}"
    assert name.startswith("dockfleet_")


# ------------------------------------------------
# Singleton lifecycle tests (new)
# ------------------------------------------------


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_get_orchestrator_returns_same_instance(mock_orch_class):
    """Repeated calls with identical args return the same instance."""
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )

    first = get_orchestrator(config=config, self_healing=True)
    second = get_orchestrator(config=config, self_healing=True)
    third = get_orchestrator()  # no args — should still return same instance

    assert first is second
    assert second is third
    # Orchestrator constructor should only be called once
    mock_orch_class.assert_called_once()


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_get_orchestrator_warns_on_config_mismatch(mock_orch_class, caplog):
    """Different config triggers a warning and returns original instance."""
    config_a = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )
    config_b = DockFleetConfig(
        services={"other": ServiceConfig(image="redis", restart=RestartPolicy.never)}
    )

    first = get_orchestrator(config=config_a, self_healing=True)
    with caplog.at_level("WARNING", logger="dockfleet.core.orchestrator"):
        second = get_orchestrator(config=config_b, self_healing=True)

    assert first is second
    assert "config" in caplog.text
    assert "arguments ignored" in caplog.text


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_get_orchestrator_warns_on_self_healing_mismatch(mock_orch_class, caplog):
    """Different self_healing triggers a warning and returns original instance."""
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )

    first = get_orchestrator(config=config, self_healing=True)
    with caplog.at_level("WARNING", logger="dockfleet.core.orchestrator"):
        second = get_orchestrator(config=config, self_healing=False)

    assert first is second
    assert "self_healing" in caplog.text
    assert "arguments ignored" in caplog.text


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_get_orchestrator_no_warning_when_identical(mock_orch_class, caplog):
    """Identical args produce no warning."""
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )

    # Set mock instance attributes so the comparison inside _warn_on_mismatch
    # sees matching values (the mock's attributes are MagicMocks by default).
    mock_instance = mock_orch_class.return_value
    mock_instance.config = config
    mock_instance.self_healing = True

    get_orchestrator(config=config, self_healing=True)
    with caplog.at_level("WARNING", logger="dockfleet.core.orchestrator"):
        get_orchestrator(config=config, self_healing=True)

    assert "arguments ignored" not in caplog.text


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_reset_orchestrator_clears_singleton(mock_orch_class):
    """After reset, next get_orchestrator creates a fresh instance."""
    config_a = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )
    config_b = DockFleetConfig(
        services={"other": ServiceConfig(image="redis", restart=RestartPolicy.never)}
    )

    # Make each Orchestrator() call return a distinct mock instance
    first_mock = MagicMock()
    first_mock.config = config_a
    first_mock.self_healing = True
    second_mock = MagicMock()
    second_mock.config = config_b
    second_mock.self_healing = False
    mock_orch_class.side_effect = [first_mock, second_mock]

    first = get_orchestrator(config=config_a, self_healing=True)
    reset_orchestrator()
    second = get_orchestrator(config=config_b, self_healing=False)

    assert first is not second
    assert mock_orch_class.call_count == 2
    # Verify the second instance got the new config
    second_args = mock_orch_class.call_args_list[1]
    assert second_args[0][0] == config_b  # positional arg is config
    assert second_args[1]["self_healing"] is False


def test_reset_orchestrator_safe_when_no_instance():
    """reset_orchestrator() is a no-op when no singleton exists."""
    reset_orchestrator()  # should not raise


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_reset_orchestrator_safe_when_instance_exists(mock_orch_class):
    """reset_orchestrator() cleanly clears an existing instance."""
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )
    get_orchestrator(config=config, self_healing=True)
    reset_orchestrator()
    # No assertion needed — just ensure no exception is raised


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_concurrent_first_call_creates_single_instance(mock_orch_class):
    """Only one Orchestrator is created even with concurrent first calls."""
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )

    barrier = threading.Barrier(10)
    results = []

    def call_get_orchestrator():
        barrier.wait()  # ensure all threads hit get_orchestrator at ~same time
        orch = get_orchestrator(config=config, self_healing=True)
        results.append(orch)

    threads = [threading.Thread(target=call_get_orchestrator) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads got the same instance
    assert len(results) == 10
    assert all(r is results[0] for r in results)
    # But Orchestrator was only constructed once
    mock_orch_class.assert_called_once()


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_config_none_defaults_to_empty_config(mock_orch_class):
    """config=None is treated as DockFleetConfig(services={})."""
    get_orchestrator(config=None, self_healing=True)
    get_orchestrator(config=None, self_healing=True)

    # Only one construction
    mock_orch_class.assert_called_once()
    call_args = mock_orch_class.call_args
    # Default config is now a proper DockFleetConfig, not a bare dict
    from dockfleet.cli.config import DockFleetConfig

    assert isinstance(call_args[0][0], DockFleetConfig)
    assert call_args[0][0].services == {}


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_get_orchestrator_defaults(mock_orch_class):
    """Default call (no args) uses DockFleetConfig(services={}) and self_healing=True."""
    orch = get_orchestrator()

    from dockfleet.cli.config import DockFleetConfig

    call_args = mock_orch_class.call_args
    assert isinstance(call_args[0][0], DockFleetConfig)
    assert call_args[0][0].services == {}
    assert call_args[1]["self_healing"] is True
    assert orch is mock_orch_class.return_value


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_no_spurious_warning_when_self_healing_omitted(mock_orch_class, caplog):
    """Regression: restart_service()/mark_restart_failed()/settings call
    get_orchestrator() without self_healing.  When the singleton was created
    with self_healing=False (e.g. --no-restart mode), these calls must NOT
    trigger a spurious self_healing mismatch warning.
    """
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )

    # Create singleton with self_healing=False
    get_orchestrator(config=config, self_healing=False)

    # Simulate restart_service() / mark_restart_failed() / /settings pattern:
    # call get_orchestrator() with no self_healing argument at all.
    with caplog.at_level("WARNING", logger="dockfleet.core.orchestrator"):
        get_orchestrator()

    assert "self_healing" not in caplog.text
    assert "arguments ignored" not in caplog.text


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_no_warning_when_explicit_self_healing_matches(mock_orch_class, caplog):
    """Explicitly passing the same self_healing value must not warn."""
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )

    # Set mock instance attributes so _warn_on_mismatch comparison works
    mock_instance = mock_orch_class.return_value
    mock_instance.config = config
    mock_instance.self_healing = False

    get_orchestrator(config=config, self_healing=False)
    with caplog.at_level("WARNING", logger="dockfleet.core.orchestrator"):
        get_orchestrator(config=config, self_healing=False)

    assert "arguments ignored" not in caplog.text


@patch("dockfleet.core.orchestrator.Orchestrator")
def test_warning_when_explicit_self_healing_differs(mock_orch_class, caplog):
    """Explicitly passing a different self_healing must warn."""
    config = DockFleetConfig(
        services={"svc": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )

    # Set mock instance attributes so _warn_on_mismatch comparison works
    mock_instance = mock_orch_class.return_value
    mock_instance.config = config
    mock_instance.self_healing = False

    get_orchestrator(config=config, self_healing=False)
    with caplog.at_level("WARNING", logger="dockfleet.core.orchestrator"):
        get_orchestrator(config=config, self_healing=True)

    assert "self_healing" in caplog.text
    assert "arguments ignored" in caplog.text


# ------------------------------------------------
# Bug 1 regression: real Orchestrator with no config
# ------------------------------------------------


def test_get_orchestrator_no_config_returns_working_instance():
    """Regression: get_orchestrator() with no config must not raise.

    Before the fix, config=None resolved to a bare dict ``{}`` which
    Orchestrator.__init__ tried to assign ``.services`` onto, raising
    AttributeError.  The fix defaults to DockFleetConfig(services={}) instead.
    """
    reset_orchestrator()
    try:
        orch = get_orchestrator()
        assert orch is not None
        # Orchestrator must have a proper config with a .services attribute
        assert hasattr(orch.config, "services")
        assert orch.config.services == {}
        assert orch.self_healing is True  # default
    finally:
        reset_orchestrator()


def test_get_orchestrator_no_config_repeated_calls_return_same():
    """Repeated no-arg calls return the same working instance."""
    reset_orchestrator()
    try:
        first = get_orchestrator()
        second = get_orchestrator()
        assert first is second
        assert hasattr(first.config, "services")
    finally:
        reset_orchestrator()


# ------------------------------------------------
# Bug 2 regression: TOCTOU race between get and reset
# ------------------------------------------------


def test_concurrent_get_reset_no_stale_instance():
    """Regression: concurrent get_orchestrator() and reset_orchestrator()
    must never return a broken or unusable instance.

    Before the TOCTOU fix, the fast-path read was unsynchronized, so a
    thread could read a non-None instance, get preempted while another
    thread reset and recreated the singleton, and return a stale reference.
    With the lock, the entire read-or-create is atomic.

    This test stresses concurrent get/reset and verifies every returned
    instance is a valid TaggedOrchestrator with proper attributes —
    proving the lock serializes access correctly.
    """
    import itertools

    import dockfleet.core.orchestrator as orch_mod

    generation_counter = itertools.count(1)

    class TaggedOrchestrator:
        """Lightweight stand-in with a generation tag."""

        def __init__(self, gen, self_healing=True):
            self.generation = gen
            self.self_healing = self_healing
            self.config = DockFleetConfig(services={})

    original_cls = orch_mod.Orchestrator

    def tagged_factory(*args, **kwargs):
        gen = next(generation_counter)
        return TaggedOrchestrator(gen, **kwargs)

    reset_orchestrator()
    orch_mod.Orchestrator = tagged_factory
    try:
        errors = []
        all_instances = []
        lock_for_collection = threading.Lock()
        stop = threading.Event()

        def getter():
            while not stop.is_set():
                orch = get_orchestrator()
                # Every returned instance must be a valid TaggedOrchestrator
                if not isinstance(orch, TaggedOrchestrator):
                    errors.append(f"returned non-TaggedOrchestrator: {type(orch)}")
                elif not hasattr(orch, "generation"):
                    errors.append("returned instance missing generation")
                else:
                    with lock_for_collection:
                        all_instances.append(orch)

        def resetting():
            while not stop.is_set():
                reset_orchestrator()

        threads = [
            threading.Thread(target=getter, daemon=True),
            threading.Thread(target=getter, daemon=True),
            threading.Thread(target=getter, daemon=True),
            threading.Thread(target=resetting, daemon=True),
            threading.Thread(target=resetting, daemon=True),
        ]
        for t in threads:
            t.start()
        stop.wait(timeout=0.3)
        stop.set()
        for t in threads:
            t.join(timeout=2)

        assert not errors, "Race condition: " + "; ".join(errors)
        # At least some instances should have been created (sanity check)
        assert len(all_instances) > 0, "No instances were created at all"
    finally:
        orch_mod.Orchestrator = original_cls
        reset_orchestrator()


@patch("dockfleet.core.orchestrator.subprocess.run")
def test_get_service_stats_unready_stats_handles_valueerror(mock_run):
    """Test that get_service_stats handles unready stats ('--', '-- / --', '--') without raising ValueError."""
    config = DockFleetConfig(
        services={
            "web": ServiceConfig(image="nginx", restart=RestartPolicy.always),
            "db": ServiceConfig(image="postgres", restart=RestartPolicy.always),
        }
    )
    orch = Orchestrator(config)

    # Simulate docker stats returning header + unready stats line for 'web'
    stats_output = (
        "CONTAINER\tCPU %\tMEM USAGE / LIMIT\tMEM %\tNET I/O\tBLOCK I/O\tPIDS\n"
        "dockfleet_web\t--\t-- / --\t--\t0B / 0B\t0B / 0B\t0\n"
    )

    def side_effect(cmd, **kwargs):
        if "stats" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = stats_output
            return mock_res
        if "inspect" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "2026-09-19T10:00:00.000Z"
            return mock_res
        return MagicMock(returncode=0, stdout="")

    mock_run.side_effect = side_effect

    stats = orch.get_service_stats()
    assert len(stats) == 2

    web_stat = next(s for s in stats if s.service_name == "web")
    assert web_stat.status == ContainerStatus.RUNNING
    assert web_stat.cpu_percent == 0.0
    assert web_stat.mem_current == "--/--"
    assert web_stat.mem_percent == "--"

    db_stat = next(s for s in stats if s.service_name == "db")
    assert db_stat.status == ContainerStatus.STOPPED


@patch("dockfleet.core.orchestrator.subprocess.run")
def test_get_service_stats_single_dash_memory(mock_run):
    """Test that get_service_stats handles memory string without a slash (e.g. '--')."""
    config = DockFleetConfig(
        services={"app": ServiceConfig(image="node", restart=RestartPolicy.always)}
    )
    orch = Orchestrator(config)

    stats_output = (
        "CONTAINER\tCPU %\tMEM USAGE / LIMIT\tMEM %\tNET I/O\tBLOCK I/O\tPIDS\n"
        "dockfleet_app\t--\t--\t--\t0B / 0B\t0B / 0B\t0\n"
    )

    def side_effect(cmd, **kwargs):
        if "stats" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = stats_output
            return mock_res
        if "inspect" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "2026-09-19T10:00:00.000Z"
            return mock_res
        return MagicMock(returncode=0, stdout="")

    mock_run.side_effect = side_effect

    stats = orch.get_service_stats()
    assert len(stats) == 1
    app_stat = stats[0]
    assert app_stat.service_name == "app"
    assert app_stat.status == ContainerStatus.RUNNING
    assert app_stat.cpu_percent == 0.0
    assert app_stat.mem_current == "--/N/A"


@patch("dockfleet.core.orchestrator.subprocess.run")
def test_get_service_stats_non_standard_memory_units(mock_run):
    """Test get_service_stats handles various non-standard memory units properly."""
    config = DockFleetConfig(
        services={
            "web": ServiceConfig(image="nginx", restart=RestartPolicy.always),
            "worker": ServiceConfig(image="python", restart=RestartPolicy.always),
            "cache": ServiceConfig(image="redis", restart=RestartPolicy.always),
        }
    )
    orch = Orchestrator(config)

    stats_output = (
        "CONTAINER\tCPU %\tMEM USAGE / LIMIT\tMEM %\tNET I/O\tBLOCK I/O\tPIDS\n"
        "dockfleet_web\t12.5%\t512MB / 2GB\t25.0%\t1MB / 2MB\t0B / 0B\t10\n"
        "dockfleet_worker\t0.0%\t1024kB / 10MB\t10.0%\t0B / 0B\t0B / 0B\t2\n"
        "dockfleet_cache\t5.0%\t256MiB\t5.0%\t0B / 0B\t0B / 0B\t4\n"
    )

    def side_effect(cmd, **kwargs):
        if "stats" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = stats_output
            return mock_res
        if "inspect" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "2026-09-19T10:00:00.000Z"
            return mock_res
        return MagicMock(returncode=0, stdout="")

    mock_run.side_effect = side_effect

    stats = orch.get_service_stats()
    assert len(stats) == 3

    web_stat = next(s for s in stats if s.service_name == "web")
    assert web_stat.status == ContainerStatus.RUNNING
    assert web_stat.cpu_percent == 12.5
    assert web_stat.mem_current == "512MB/2GB"
    assert web_stat.mem_percent == "25.0%"

    worker_stat = next(s for s in stats if s.service_name == "worker")
    assert worker_stat.status == ContainerStatus.RUNNING
    assert worker_stat.cpu_percent == 0.0
    assert worker_stat.mem_current == "1024kB/10MB"
    assert worker_stat.mem_percent == "10.0%"

    cache_stat = next(s for s in stats if s.service_name == "cache")
    assert cache_stat.status == ContainerStatus.RUNNING
    assert cache_stat.cpu_percent == 5.0
    assert cache_stat.mem_current == "256MiB/N/A"
    assert cache_stat.mem_percent == "5.0%"


@patch("dockfleet.core.orchestrator.subprocess.run")
def test_get_service_stats_unparseable_container_fallback(mock_run):
    """Test that an unparseable container stats line falls back gracefully per-container while retaining valid stats."""
    config = DockFleetConfig(
        services={
            "healthy": ServiceConfig(image="nginx", restart=RestartPolicy.always),
            "corrupt": ServiceConfig(image="broken", restart=RestartPolicy.always),
            "stopped": ServiceConfig(image="postgres", restart=RestartPolicy.always),
        }
    )
    orch = Orchestrator(config)

    # 'healthy' is valid, 'corrupt' has invalid CPU and malformed columns, 'stopped' is not running
    stats_output = (
        "CONTAINER\tCPU %\tMEM USAGE / LIMIT\tMEM %\tNET I/O\tBLOCK I/O\tPIDS\n"
        "dockfleet_healthy\t1.5%\t50MiB / 200MiB\t25.0%\t10kB / 20kB\t0B / 0B\t5\n"
        "dockfleet_corrupt\tinvalid.cpu.val%\t\n"
    )

    def side_effect(cmd, **kwargs):
        if "stats" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = stats_output
            return mock_res
        if "inspect" in cmd:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "2026-09-19T10:00:00.000Z"
            return mock_res
        return MagicMock(returncode=0, stdout="")

    mock_run.side_effect = side_effect

    stats = orch.get_service_stats()
    assert len(stats) == 3

    healthy_stat = next(s for s in stats if s.service_name == "healthy")
    assert healthy_stat.status == ContainerStatus.RUNNING
    assert healthy_stat.cpu_percent == 1.5
    assert healthy_stat.mem_current == "50MiB/200MiB"
    assert healthy_stat.mem_percent == "25.0%"

    corrupt_stat = next(s for s in stats if s.service_name == "corrupt")
    assert corrupt_stat.status == ContainerStatus.RUNNING
    assert corrupt_stat.cpu_percent == 0.0
    assert corrupt_stat.mem_current == "N/A/N/A"

    stopped_stat = next(s for s in stats if s.service_name == "stopped")
    assert stopped_stat.status == ContainerStatus.STOPPED


@patch("dockfleet.core.orchestrator.subprocess.run")
def test_monitor_services_empty_and_blank_lines(mock_run):
    """Test that monitor_services does not crash on empty, whitespace, or malformed docker ps output."""
    config = DockFleetConfig(
        services={"api": ServiceConfig(image="nginx", restart=RestartPolicy.always)}
    )
    orch = Orchestrator(config)
    orch.handle_unhealthy_service = MagicMock()

    # 1. Completely empty stdout
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    orch.monitor_services()
    orch.handle_unhealthy_service.assert_not_called()

    # 2. Trailing whitespace and blank lines
    mock_run.return_value = MagicMock(returncode=0, stdout="\n   \n\t\n\r\n")
    orch.monitor_services()
    orch.handle_unhealthy_service.assert_not_called()

    # 3. Malformed line without tab delimiter
    mock_run.return_value = MagicMock(returncode=0, stdout="dockfleet_api_no_tab_here\n")
    orch.monitor_services()
    orch.handle_unhealthy_service.assert_not_called()

    # 4. Valid exited service line with blank lines
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="\n\ndockfleet_api\tExited (1) 2 seconds ago\n\n",
    )
    orch.monitor_services()
    orch.handle_unhealthy_service.assert_called_once_with("api", reason="health failure")


@patch("dockfleet.core.orchestrator.subprocess.Popen")
def test_get_logs_normal_execution(mock_popen):
    """Test get_logs yields lines and closes process on normal stream completion."""
    from dockfleet.core.orchestrator import get_logs

    mock_proc = MagicMock()
    mock_proc.stdout.readline.side_effect = ["line 1\n", "line 2\n", ""]
    mock_proc.poll.return_value = 0
    mock_popen.return_value = mock_proc

    gen = get_logs("web", lines=50, follow=False)
    lines = list(gen)

    assert lines == ["line 1", "line 2"]
    mock_popen.assert_called_once_with(
        ["docker", "logs", "dockfleet_web", "--tail", "50"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    mock_proc.stdout.close.assert_called_once()


@patch("dockfleet.core.orchestrator.subprocess.Popen")
def test_get_logs_early_generator_break_cleans_up_process(mock_popen):
    """Test get_logs terminates running process in finally block if generator caller breaks early."""
    from dockfleet.core.orchestrator import get_logs

    mock_proc = MagicMock()
    mock_proc.stdout.readline.side_effect = ["line 1\n", "line 2\n", "line 3\n"]
    mock_proc.poll.return_value = None  # process still running
    mock_popen.return_value = mock_proc

    gen = get_logs("web", follow=True)
    first_line = next(gen)
    assert first_line == "line 1"

    # Close the generator prematurely (simulating client disconnect)
    gen.close()

    mock_proc.stdout.close.assert_called_once()
    mock_proc.terminate.assert_called_once()


@patch("dockfleet.core.orchestrator.subprocess.Popen")
def test_get_logs_popen_exception_handled_gracefully(mock_popen):
    """Test get_logs handles Popen exceptions gracefully and yields error message."""
    from dockfleet.core.orchestrator import get_logs

    mock_popen.side_effect = FileNotFoundError("docker executable not found")

    gen = get_logs("web")
    lines = list(gen)

    assert len(lines) == 1
    assert "Error: docker executable not found" in lines[0]


@patch("dockfleet.core.orchestrator.subprocess.Popen")
def test_get_logs_stdout_close_exception_does_not_prevent_process_termination(mock_popen):
    """Test that an exception during process.stdout.close() does not skip process termination."""
    from dockfleet.core.orchestrator import get_logs

    mock_proc = MagicMock()
    mock_proc.stdout.readline.side_effect = ["line 1\n", "line 2\n"]
    mock_proc.stdout.close.side_effect = OSError("stdout close error")
    mock_proc.poll.return_value = None  # process still running
    mock_popen.return_value = mock_proc

    gen = get_logs("web", follow=True)
    first_line = next(gen)
    assert first_line == "line 1"

    # Close the generator prematurely (simulating client disconnect)
    gen.close()

    mock_proc.stdout.close.assert_called_once()
    mock_proc.terminate.assert_called_once()


def test_down_stops_in_reverse_dependency_order():
    """Verify that orchestrator down() stops services in reverse topological dependency order."""
    config = DockFleetConfig(
        services={
            "db": ServiceConfig(image="postgres", restart=RestartPolicy.always),
            "api": ServiceConfig(image="node", depends_on=["db"], restart=RestartPolicy.always),
            "frontend": ServiceConfig(image="nginx", depends_on=["api"], restart=RestartPolicy.always),
        }
    )
    orch = Orchestrator(config)
    stopped_order = []

    def mock_stop_service(name):
        stopped_order.append(name)
        return True

    orch.stop_service = mock_stop_service
    orch.down()

    # Dependencies: frontend -> api -> db
    # Reverse topological order: frontend first, then api, then db
    assert stopped_order == ["frontend", "api", "db"]


def test_down_stops_api_before_db():
    """Verify that api is stopped before db when api depends on db."""
    config = DockFleetConfig(
        services={
            "db": ServiceConfig(image="postgres", restart=RestartPolicy.always),
            "api": ServiceConfig(image="python:3.11", depends_on=["db"], restart=RestartPolicy.always),
        }
    )
    orch = Orchestrator(config)
    stopped_order = []

    def mock_stop_service(name):
        stopped_order.append(name)
        return True

    orch.stop_service = mock_stop_service
    orch.down()

    assert stopped_order == ["api", "db"]


def test_extract_host_ports():
    """Verify that _extract_host_ports extracts host ports and host IPs correctly from various configs."""
    from dockfleet.core.orchestrator import _extract_host_ports

    # 1. Standard host:container string
    assert _extract_host_ports({"ports": ["8080:80", "9000:9000"]}) == [("0.0.0.0", 8080), ("0.0.0.0", 9000)]

    # 2. Host with IP binding (loopback)
    assert _extract_host_ports({"ports": ["127.0.0.1:3000:3000"]}) == [("127.0.0.1", 3000)]

    # 3. Port with protocol suffix
    assert _extract_host_ports({"ports": ["5432:5432/tcp"]}) == [("0.0.0.0", 5432)]

    # 4. Dictionary format
    assert _extract_host_ports({"ports": {"8000": "80"}}) == [("0.0.0.0", 8000)]
    assert _extract_host_ports({"ports": {"127.0.0.1:8000": "80"}}) == [("127.0.0.1", 8000)]

    # 5. Single port mapping without explicit host port (e.g. container port only)
    assert _extract_host_ports({"ports": ["80", "443/tcp", 8080]}) == []
    assert _extract_host_ports({"ports": ["127.0.0.1:80"]}) == []
    assert _extract_host_ports({"ports": ["127.0.0.1::80"]}) == []

    # 6. Empty or missing ports
    assert _extract_host_ports({}) == []
    assert _extract_host_ports({"ports": None}) == []


def test_is_port_released_and_wait_for_ports_released():
    """Verify is_port_released and wait_for_ports_released correctly detect socket availability."""
    import socket
    from dockfleet.core.orchestrator import is_port_released, wait_for_ports_released

    # Bind a temporary socket to make a port busy on loopback
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        busy_port = s.getsockname()[1]
        s.listen(1)

        # While busy on 127.0.0.1, is_port_released should report False
        assert is_port_released(busy_port, host="127.0.0.1") is False
        assert wait_for_ports_released([("127.0.0.1", busy_port)], timeout=0.1) is False

    # Once closed, port should be released
    assert is_port_released(busy_port, host="127.0.0.1") is True
    assert wait_for_ports_released([("127.0.0.1", busy_port)], timeout=1.0) is True


def test_orchestrator_restart_verifies_release_before_up():
    """Verify that orchestrator restart() stops services, verifies release, and starts services."""
    config = DockFleetConfig(
        services={
            "web": ServiceConfig(image="nginx", ports=["8080:80"], restart=RestartPolicy.always),
        }
    )
    orch = Orchestrator(config)
    call_order = []

    def mock_down():
        call_order.append("down")

    def mock_wait_for_release(timeout=5.0):
        call_order.append("wait_for_release")
        return True

    def mock_up():
        call_order.append("up")

    orch.down = mock_down
    orch.wait_for_release = mock_wait_for_release
    orch.up = mock_up

    orch.restart()

    assert call_order == ["down", "wait_for_release", "up"]






