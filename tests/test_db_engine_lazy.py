import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import select

from dockfleet.health.models import (
    DB_PATH,
    PROJECT_ROOT,
    ContainerStatus,
    Service,
    get_engine,
    get_session,
    init_db,
    reset_engine_cache,
)


def test_import_models_has_no_disk_side_effects(tmp_path):
    """
    Verify that importing dockfleet.health.models in a clean Python process
    does not create dockfleet.db or open a database connection.
    """
    code = """
import os
import sys
from pathlib import Path

# Remove dockfleet.db if it exists in current dir
cwd_db = Path("dockfleet.db")
if cwd_db.exists():
    os.remove(cwd_db)

# Pure import of models module
import dockfleet.health.models as models

# Check if dockfleet.db was created on disk
assert not cwd_db.exists(), "dockfleet.db was created on module import!"
assert not (models.PROJECT_ROOT / "dockfleet.db").exists(), "dockfleet.db was created on module import!"

# Inspecting classes must also not trigger engine creation
_ = models.Service
_ = models.ContainerStatus
_ = models.HealthStatus
_ = models.RestartEvent
_ = models.LogEvent

# Confirm engine cache remains empty until get_engine() is called
assert len(models._engines) == 0, f"Expected 0 cached engines, got {len(models._engines)}"
print("IMPORT_CLEAN_SUCCESS")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "IMPORT_CLEAN_SUCCESS" in result.stdout


def test_precedence_explicit_parameter_overrides_env_and_default(monkeypatch):
    """
    Precedence level 1: Explicit parameter must override DOCKFLEET_DB_URL and default path.
    """
    monkeypatch.setenv("DOCKFLEET_DB_URL", "sqlite:///env_override.db")
    reset_engine_cache()

    explicit_url = "sqlite:///:memory:"
    engine = get_engine(explicit_url)
    assert str(engine.url) == explicit_url
    assert str(engine.url) != "sqlite:///env_override.db"
    assert str(engine.url) != f"sqlite:///{DB_PATH}"


def test_precedence_env_var_overrides_default(monkeypatch):
    """
    Precedence level 2: DOCKFLEET_DB_URL environment variable must override default DB_PATH.
    """
    monkeypatch.setenv("DOCKFLEET_DB_URL", "sqlite:///custom_env.db")
    reset_engine_cache()

    engine = get_engine()
    assert str(engine.url) == "sqlite:///custom_env.db"
    assert str(engine.url) != f"sqlite:///{DB_PATH}"


def test_precedence_default_when_no_param_and_no_env(monkeypatch):
    """
    Precedence level 3: When no parameter and no env var are provided, default to PROJECT_ROOT / dockfleet.db.
    """
    monkeypatch.delenv("DOCKFLEET_DB_URL", raising=False)
    reset_engine_cache()

    engine = get_engine()
    expected_url = f"sqlite:///{DB_PATH}"
    assert str(engine.url) == expected_url


def test_get_session_context_manager(tmp_path):
    """
    Verify get_session() yields an active transactional Session and commits changes.
    """
    test_db = tmp_path / "session_test.db"
    test_url = f"sqlite:///{test_db}"

    init_db(db_url=test_url)

    with get_session(db_url=test_url) as session:
        svc = Service(name="session-test-svc", image="alpine:latest", restart_policy="always")
        session.add(svc)
        session.commit()

    with get_session(db_url=test_url) as session:
        fetched = session.exec(select(Service).where(Service.name == "session-test-svc")).one()
        assert fetched.name == "session-test-svc"
        assert fetched.status == ContainerStatus.UNKNOWN.value or fetched.status == ContainerStatus.UNKNOWN


def test_init_db_supports_custom_url_and_engine(tmp_path):
    """
    Verify init_db() works with custom db_url and explicit engine for backward compatibility.
    """
    # 1. Custom URL
    url_db = tmp_path / "custom_init.db"
    init_db(db_url=f"sqlite:///{url_db}")
    assert url_db.exists()

    # 2. Explicit Engine
    eng_db = tmp_path / "engine_init.db"
    custom_engine = get_engine(f"sqlite:///{eng_db}")
    init_db(engine=custom_engine)
    assert eng_db.exists()


def test_engine_backward_compatibility_getattr(monkeypatch):
    """
    Verify that accessing `dockfleet.health.models.engine` dynamically works via __getattr__
    for backward compatibility with any legacy code.
    """
    import dockfleet.health.models as models

    monkeypatch.setenv("DOCKFLEET_DB_URL", "sqlite:///:memory:")
    reset_engine_cache()

    legacy_engine = models.engine
    assert isinstance(legacy_engine, Engine)
    assert str(legacy_engine.url) == "sqlite:///:memory:"


def test_reset_engine_cache_disposes_and_clears():
    """
    Verify that reset_engine_cache() disposes existing engines and clears the cache dict.
    """
    reset_engine_cache()
    eng1 = get_engine("sqlite:///:memory:")
    assert eng1 is not None

    import dockfleet.health.models as models
    assert len(models._engines) == 1

    reset_engine_cache()
    assert len(models._engines) == 0


def test_isolated_test_suite_does_not_touch_root_db():
    """
    Verify that tests running with isolated_test_db autouse fixture in conftest.py
    never create or pollute root dockfleet.db.
    """
    root_db = PROJECT_ROOT / "dockfleet.db"
    # Ensure tables are initialized via isolated fixture
    init_db()
    with get_session() as session:
        session.add(Service(name="isolation-check", image="busybox", restart_policy="always"))
        session.commit()

    # Root dockfleet.db must NOT be created or modified
    assert not root_db.exists(), f"Root database {root_db} was mutated by tests!"


def test_in_memory_sqlite_static_pool_shares_state():
    """
    Verify that in-memory SQLite uses StaticPool so that tables created in
    init_db() persist across subsequent, independent get_session() scopes.
    Without StaticPool, each connection gets a blank DB and queries fail with 'no such table'.
    """
    mem_url = "sqlite:///:memory:"
    init_db(db_url=mem_url)

    with get_session(db_url=mem_url) as s1:
        s1.add(Service(name="staticpool-svc", image="nginx", restart_policy="always"))
        s1.commit()

    with get_session(db_url=mem_url) as s2:
        found = s2.exec(select(Service).where(Service.name == "staticpool-svc")).one_or_none()
        assert found is not None
        assert found.name == "staticpool-svc"


def test_cache_key_includes_echo_flag():
    """
    Verify that get_engine differentiates between echo=True and echo=False,
    caching them under distinct keys instead of ignoring echo on subsequent calls.
    """
    reset_engine_cache()
    url = "sqlite:///:memory:"

    eng_quiet = get_engine(url, echo=False)
    eng_loud = get_engine(url, echo=True)

    assert eng_quiet is not eng_loud
    assert eng_quiet.echo is False
    assert eng_loud.echo is True

    # Repeated calls return cached instances
    assert get_engine(url, echo=False) is eng_quiet
    assert get_engine(url, echo=True) is eng_loud


def test_parameter_precedence_engine_over_db_url(tmp_path):
    """
    Verify that passing both `engine` and `db_url` gives highest precedence to `engine`,
    ignoring `db_url`.
    """
    winner_path = tmp_path / "winner.db"
    loser_path = tmp_path / "loser.db"

    winner_engine = get_engine(f"sqlite:///{winner_path}")

    # Pass both to init_db: winner_engine must be initialized, loser_path must not be touched
    init_db(db_url=f"sqlite:///{loser_path}", engine=winner_engine)
    assert winner_path.exists()
    assert not loser_path.exists()

    # Pass both to get_session: operations must hit winner_engine
    with get_session(db_url=f"sqlite:///{loser_path}", engine=winner_engine) as session:
        session.add(Service(name="winner-svc", image="alpine", restart_policy="always"))
        session.commit()

    assert not loser_path.exists()


def test_thread_safe_concurrent_engine_initialization():
    """
    Verify that concurrent first-access calls to get_engine() across multiple
    threads safely return the exact same Engine instance under _engines_lock.
    """
    import threading

    reset_engine_cache()
    test_url = "sqlite:///:memory:"
    results = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        eng = get_engine(test_url)
        results.append(eng)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)

    assert len(results) == 10
    first_engine = results[0]
    assert all(eng is first_engine for eng in results), "Engine instance was not shared across threads!"

