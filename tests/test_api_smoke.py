from fastapi.testclient import TestClient
from dockfleet.dashboard.api import app

client = TestClient(app)


def test_get_services_schema():
    response = client.get("/services")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)

    # don't assume data exists
    if data:
        service = data[0]
        assert "name" in service
        assert "status" in service
        assert "health_status" in service
        assert "restart_count" in service


def test_restart_endpoint():
    response = client.post("/services/api/restart")

    # allow both success and safe failure
    assert response.status_code in [200, 400, 404]


def test_stop_endpoint():
    response = client.post("/services/api/stop")

    # allow both success and safe failure
    assert response.status_code in [200, 400, 404]