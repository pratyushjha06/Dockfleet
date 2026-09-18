from pathlib import Path

import pytest
import typer
from pydantic import ValidationError

from dockfleet.cli.config import DockFleetConfig, format_validation_error, load_config


def test_valid_config():
    config = load_config(Path("examples/dockfleet.yaml"))
    assert "api" in config.services
    assert config.services["api"].image == "nginx"


def test_missing_image():
    bad_yaml = """
services:
  api:
    restart: always
"""
    path = Path("tests/tmp_bad.yaml")
    path.write_text(bad_yaml)

    with pytest.raises(typer.Exit) as exc_info:
        load_config(path)
    assert exc_info.value.exit_code == 1


def test_missing_restart():
    bad_yaml = """
services:
  api:
    image: my-api:latest
"""
    path = Path("tests/tmp_bad2.yaml")
    path.write_text(bad_yaml)
    with pytest.raises(typer.Exit) as exc_info:
        load_config(path)
    assert exc_info.value.exit_code == 1


# ---------------------------------------------------------------------------
# Field-specific, actionable error messages (issue #162)
# ---------------------------------------------------------------------------


def test_missing_field_names_service_and_field():
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig(
            **{"services": {"api": {"restart": "always"}}}
        )
    lines = format_validation_error(exc_info.value)
    assert lines == ["service 'api': missing required field 'image'"]


def test_unknown_field_suggests_closest_match():
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig(
            **{
                "services": {
                    "api": {"image": "nginx", "restrat": "always"}
                }
            }
        )
    lines = format_validation_error(exc_info.value)
    assert any(
        "unknown field 'restrat'" in line and "did you mean 'restart'?" in line
        for line in lines
    )


def test_unknown_nested_healthcheck_field_suggests_match():
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig(
            **{
                "services": {
                    "api": {
                        "image": "nginx",
                        "restart": "always",
                        "healthcheck": {"typ": "http", "interval": 5},
                    }
                }
            }
        )
    lines = format_validation_error(exc_info.value)
    assert any(
        "unknown field 'healthcheck.typ'" in line and "did you mean 'type'?" in line
        for line in lines
    )


def test_invalid_restart_policy_lists_allowed_values():
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig(
            **{
                "services": {
                    "api": {"image": "nginx", "restart": "sometimes"}
                }
            }
        )
    lines = format_validation_error(exc_info.value)
    assert len(lines) == 1
    assert "field 'restart'" in lines[0]
    assert "sometimes" in lines[0]
    assert "always" in lines[0] and "on-failure" in lines[0] and "never" in lines[0]


def test_wrong_type_names_field_and_given_value():
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig(
            **{
                "services": {
                    "api": {
                        "image": "nginx",
                        "restart": "always",
                        "resources": {"cpu": "notanumber"},
                    }
                }
            }
        )
    lines = format_validation_error(exc_info.value)
    assert lines == [
        "service 'api': field 'resources.cpu' expected a number, got 'notanumber'"
    ]


def test_union_type_field_merges_branch_errors_into_one_line():
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig(
            **{
                "services": {
                    "api": {
                        "image": "nginx",
                        "restart": "always",
                        "environment": 123,
                    }
                }
            }
        )
    lines = format_validation_error(exc_info.value)
    # A `list[str] | dict[str, str]` field produces one raw pydantic error
    # per union branch; these must collapse into a single readable line.
    assert len(lines) == 1
    assert "field 'environment'" in lines[0]
    assert "123" in lines[0]


def test_custom_validator_message_is_field_specific():
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig(
            **{
                "services": {
                    "api": {
                        "image": "nginx",
                        "restart": "always",
                        "resources": {"memory": "500mb"},
                    }
                }
            }
        )
    lines = format_validation_error(exc_info.value)
    assert lines == [
        "service 'api': field 'resources.memory' — invalid memory limit "
        "(expected like 512m or 1g)"
    ]
