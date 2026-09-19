import pytest

from dockfleet.cli.config import DockFleetConfig


def test_valid_resources():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "resources": {"memory": "512m", "cpu": 0.5},
            }
        }
    }

    DockFleetConfig(**config)


def test_invalid_memory():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "resources": {"memory": "500mb"},
            }
        }
    }

    with pytest.raises(ValueError):
        DockFleetConfig(**config)


def test_invalid_cpu():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "resources": {"cpu": -1},
            }
        }
    }

    with pytest.raises(ValueError):
        DockFleetConfig(**config)


def test_invalid_depends_on():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "depends_on": ["redis"],
            }
        }
    }

    with pytest.raises(ValueError):
        DockFleetConfig(**config)


def test_valid_environment_list():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "environment": ["KEY=VALUE"],
            }
        }
    }

    DockFleetConfig(**config)


def test_invalid_environment():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "environment": ["INVALID"],
            }
        }
    }

    with pytest.raises(ValueError):
        DockFleetConfig(**config)


def test_valid_self_healing_controls():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "self_healing": True,
                "max_restarts": 5,
                "backoff_seconds": 10,
                "backoff_multiplier": 2.0,
            }
        }
    }

    parsed = DockFleetConfig(**config)

    service = parsed.services["api"]
    assert service.max_restarts == 5
    assert service.backoff_seconds == 10
    assert service.backoff_multiplier == 2.0


def test_self_healing_controls_default_to_none():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
            }
        }
    }

    parsed = DockFleetConfig(**config)

    service = parsed.services["api"]
    assert service.max_restarts is None
    assert service.backoff_seconds is None
    assert service.backoff_multiplier is None


def test_invalid_max_restarts():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "max_restarts": 0,
            }
        }
    }

    with pytest.raises(ValueError):
        DockFleetConfig(**config)


def test_invalid_backoff_seconds():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "backoff_seconds": -1,
            }
        }
    }

    with pytest.raises(ValueError):
        DockFleetConfig(**config)


def test_invalid_backoff_multiplier():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "backoff_multiplier": 0.5,
            }
        }
    }

    with pytest.raises(ValueError):
        DockFleetConfig(**config)


def test_tcp_healthcheck_missing_endpoint():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "healthcheck": {
                    "type": "tcp",
                    "interval": 10,
                },
            }
        }
    }
    with pytest.raises(ValueError, match="endpoint"):
        DockFleetConfig(**config)


def test_http_healthcheck_missing_endpoint():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "healthcheck": {
                    "type": "http",
                    "interval": 10,
                },
            }
        }
    }
    with pytest.raises(ValueError, match="endpoint"):
        DockFleetConfig(**config)


def test_process_healthcheck_without_endpoint():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "healthcheck": {
                    "type": "process",
                    "interval": 10,
                },
            }
        }
    }
    parsed = DockFleetConfig(**config)
    assert parsed.services["api"].healthcheck.endpoint is None


def test_valid_tcp_and_http_healthchecks_with_endpoint():
    config = {
        "services": {
            "web": {
                "image": "nginx",
                "restart": "always",
                "healthcheck": {
                    "type": "http",
                    "endpoint": "http://localhost:8080/health",
                    "interval": 10,
                },
            },
            "db": {
                "image": "postgres:15",
                "restart": "always",
                "healthcheck": {
                    "type": "tcp",
                    "endpoint": "localhost:5432",
                    "interval": 30,
                },
            },
        }
    }
    parsed = DockFleetConfig(**config)
    assert parsed.services["web"].healthcheck.endpoint == "http://localhost:8080/health"
    assert parsed.services["db"].healthcheck.endpoint == "localhost:5432"
