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
