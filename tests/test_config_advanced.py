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