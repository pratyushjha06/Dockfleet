import pytest
from dockfleet.health.models import get_engine, get_session, init_db, reset_engine_cache


@pytest.fixture(autouse=True)
def isolated_test_db(tmp_path, monkeypatch):
    """
    Ensure all tests run against an isolated SQLite database file within tmp_path,
    preventing any mutation or pollution of the root developer dockfleet.db.
    """
    test_db_path = tmp_path / "test_dockfleet.db"
    test_db_url = f"sqlite:///{test_db_path}"
    monkeypatch.setenv("DOCKFLEET_DB_URL", test_db_url)
    reset_engine_cache()
    init_db()
    yield test_db_url
    reset_engine_cache()
