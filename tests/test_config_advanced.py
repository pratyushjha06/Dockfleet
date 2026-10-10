import pytest

from dockfleet.cli.config import DockFleetConfig


@pytest.mark.parametrize(
    "valid_mem",
    [
        "512m",
        "512mb",
        "1g",
        "1gb",
        "1024k",
        "1024kb",
        "1048576b",
        "512M",
        "1GB",
        "256MiB",
        "0.5g",
        "0.5G",
        "1.5gb",
        "0.5m",
        ".5g",
        "1048576",
        "1024",
    ],
)
def test_valid_resources(valid_mem):
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "resources": {"memory": valid_mem, "cpu": 0.5},
            }
        }
    }

    DockFleetConfig(**config)


@pytest.mark.parametrize("invalid_mem", ["500xyz", "abc", "-512m", "512 megabytes", "mb"])
def test_invalid_memory(invalid_mem):
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "resources": {"memory": invalid_mem},
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


def test_circular_depends_on_is_rejected_during_validation():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "depends_on": ["worker"],
            },
            "worker": {
                "image": "nginx",
                "restart": "always",
                "depends_on": ["api"],
            },
        }
    }

    with pytest.raises(ValueError, match="circular depends_on relationship: api -> worker -> api"):
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


def test_valid_environment_dict_with_scalars():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "environment": {
                    "PORT": 8080,
                    "DEBUG": True,
                    "RATE": 1.5,
                    "NAME": "test",
                },
            }
        }
    }

    parsed = DockFleetConfig(**config)
    assert parsed.services["api"].environment == {
        "PORT": "8080",
        "DEBUG": "True",
        "RATE": "1.5",
        "NAME": "test",
    }


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


def test_invalid_environment_dict_with_nested_structure():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "environment": {"PORT": [8080]},
            }
        }
    }

    with pytest.raises(ValueError, match="Invalid environment dict format"):
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


def test_valid_ports():
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "ports": ["80:80", "1:1", "65535:65535", "8080:80"],
            }
        }
    }
    parsed = DockFleetConfig(**config)
    assert parsed.services["api"].ports == ["80:80", "1:1", "65535:65535", "8080:80"]


@pytest.mark.parametrize(
    "invalid_port",
    ["0:80", "80:0", "65536:80", "80:65536", "70000:8080", "80:99999"],
)
def test_out_of_range_ports(invalid_port):
    config = {
        "services": {
            "api": {
                "image": "nginx",
                "restart": "always",
                "ports": [invalid_port],
            }
        }
    }
    with pytest.raises(ValueError, match="Port values must be between 1 and 65535"):
        DockFleetConfig(**config)

