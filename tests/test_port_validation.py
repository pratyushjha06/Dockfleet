"""Tests for Docker-style port mapping validation in the service config.

The validator used to accept only two bare integer ports, so standard Docker
bindings with a host IP, a protocol suffix or a dictionary mapping failed at
config load time.  These tests cover the accepted forms and the malformed
values that must still be rejected.
"""

import pytest
from pydantic import ValidationError

from dockfleet.cli.config import RestartPolicy, ServiceConfig, load_config


def _service(ports):
    return ServiceConfig(
        image="nginx:alpine",
        restart=RestartPolicy.always,
        ports=ports,
    )


@pytest.mark.parametrize(
    "ports",
    [
        ["8080:80"],
        ["127.0.0.1:8080:80"],
        ["0.0.0.0:3000:3000"],
        ["5432:5432/tcp"],
        ["53:53/udp"],
        ["8080:80/sctp"],
        ["[::1]:8080:80"],
    ],
)
def test_standard_docker_port_mappings_are_accepted(ports):
    assert _service(ports).ports == ports


def test_dictionary_ports_are_normalized_to_strings():
    service = _service({"8000": "80", "127.0.0.1:9000": 90})

    assert service.ports == ["8000:80", "127.0.0.1:9000:90"]


def test_yaml_with_ip_protocol_and_dictionary_bindings_loads(tmp_path):
    path = tmp_path / "dockfleet.yaml"
    path.write_text("""
services:
  web:
    image: nginx:alpine
    restart: always
    ports:
      - "127.0.0.1:8080:80"
      - "5432:5432/tcp"
  db:
    image: postgres:16
    restart: always
    ports:
      "127.0.0.1:5432": 5432
""")

    config = load_config(path)

    assert config.services["web"].ports == ["127.0.0.1:8080:80", "5432:5432/tcp"]
    assert config.services["db"].ports == ["127.0.0.1:5432:5432"]


@pytest.mark.parametrize(
    "ports",
    [
        [8080],
        ["8080"],
        [":8080:80"],
        ["8080:80:90:100"],
        ["127.0.0.1:70000:80"],
        ["0:80"],
        ["8080:80/http"],
        ["[::1]8080:80"],
    ],
)
def test_invalid_port_mappings_are_rejected(ports):
    with pytest.raises(ValidationError):
        _service(ports)
