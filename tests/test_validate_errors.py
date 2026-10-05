from pathlib import Path

import pytest
import typer
from pydantic import ValidationError
from typer.testing import CliRunner

from dockfleet.cli.config import (
    DockFleetConfig,
    HealthCheckConfig,
    ResourcesConfig,
    ServiceConfig,
    format_input_value,
    format_loc,
    load_config,
)
from dockfleet.cli.main import app

runner = CliRunner()


def test_validate_success():
    """Valid configuration succeeds with exit code 0."""
    result = runner.invoke(app, ["validate", "examples/dockfleet.yaml"])
    assert result.exit_code == 0
    assert "Config valid" in result.stdout


def test_validate_missing_required_field(tmp_path: Path):
    """Missing required field shows an actionable message with the exact field path."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "Configuration Validation Error" in result.output
    assert "Validation failed:" in result.output
    assert "services.web.restart: required field is missing" in result.output


def test_validate_missing_services_root(tmp_path: Path):
    """Missing top-level services block shows clear missing field error."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
self_healing: true
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services: required field is missing" in result.output


def test_validate_wrong_field_type_integer(tmp_path: Path):
    """Wrong field type (string instead of int) displays expected and actual types."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    max_restarts: "three"
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.max_restarts" in result.output
    assert 'expected an integer, got string ("three")' in result.output


def test_validate_wrong_field_type_boolean(tmp_path: Path):
    """Wrong field type (string instead of boolean) displays expected and actual types."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    self_healing: "notabool"
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.self_healing" in result.output
    assert 'expected a boolean, got string ("notabool")' in result.output


def test_validate_wrong_field_type_list(tmp_path: Path):
    """Wrong field type (string instead of list) displays expected list."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    ports: "80:80"
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.ports" in result.output
    assert 'expected a list, got string ("80:80")' in result.output


def test_validate_unknown_field_with_suggestion(tmp_path: Path):
    """Unknown field with typo shows unknown field and did you mean suggestion."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    imgae: nginx
    restart: always
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.imgae: unknown field" in result.output
    assert 'Did you mean "image"?' in result.output


def test_validate_unknown_field_without_suggestion(tmp_path: Path):
    """Unknown field with no close match shows unknown field without confusing suggestions."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    arbitrary_extra_field: 123
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.arbitrary_extra_field: unknown field" in result.output
    assert "Did you mean" not in result.output


def test_validate_unknown_field_top_level(tmp_path: Path):
    """Unknown field at top-level dockfleet configuration is rejected."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
unknown_top_level: true
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "unknown_top_level: unknown field" in result.output


def test_validate_unknown_field_top_level_typo(tmp_path: Path):
    """Typo in top-level field produces a suggestion."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
servicess:
  web:
    image: nginx
    restart: always
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert 'servicess: unknown field. Did you mean "services"?' in result.output


def test_validate_unknown_field_healthcheck(tmp_path: Path):
    """Unknown field in nested healthcheck configuration is rejected."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    healthcheck:
      type: http
      endpoint: http://localhost:80
      interval: 10
      extra_health_field: 123
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.healthcheck.extra_health_field: unknown field" in result.output


def test_validate_unknown_field_healthcheck_typo(tmp_path: Path):
    """Typo in healthcheck field produces a suggestion."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    healthcheck:
      type: http
      endpont: http://localhost:80
      interval: 10
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert 'services.web.healthcheck.endpont: unknown field. Did you mean "endpoint"?' in result.output


def test_validate_unknown_field_resources(tmp_path: Path):
    """Unknown field in nested resources configuration is rejected."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    resources:
      cpu: 1.0
      extra_resource_field: 456
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.resources.extra_resource_field: unknown field" in result.output


def test_validate_unknown_field_resources_typo(tmp_path: Path):
    """Typo in resources field produces a suggestion."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    resources:
      cpus: 1.0
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert 'services.web.resources.cpus: unknown field. Did you mean "cpu"?' in result.output


def test_validate_invalid_enum_value(tmp_path: Path):
    """Invalid enum value shows offending input and allowed choices from schema."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: invalid
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert 'services.web.restart: invalid value "invalid"' in result.output
    assert "expected one of: always, on-failure, never" in result.output


def test_validate_multiple_errors(tmp_path: Path):
    """Multiple validation errors in one configuration are all reported."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    restart: sometimes
    max_restarts: "three"
    imgae: nginx
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.image: required field is missing" in result.output
    assert 'services.web.restart: invalid value "sometimes"' in result.output
    assert 'services.web.max_restarts: expected an integer, got string ("three")' in result.output
    assert "services.web.imgae: unknown field" in result.output


def test_validate_nested_paths_in_lists_and_submodels(tmp_path: Path):
    """Nested paths in submodels and lists format as dot/bracket notation."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web:
    image: nginx
    restart: always
    ports:
      - 123
    resources:
      cpu: "fast"
    healthcheck:
      type: "http"
      interval: "thirty"
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "services.web.ports[0]: expected a string, got integer (123)" in result.output
    assert 'services.web.resources.cpu: expected a number, got string ("fast")' in result.output
    assert 'services.web.healthcheck.interval: expected an integer, got string ("thirty")' in result.output


def test_validate_malformed_yaml_syntax(tmp_path: Path):
    """Malformed YAML syntax is clearly identified as a YAML error, not Pydantic validation."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
services:
  web: [unclosed_bracket
""")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "Invalid YAML syntax in" in result.output
    assert "Configuration Validation Error" not in result.output
    assert "Validation failed:" not in result.output


def test_validate_non_dict_yaml(tmp_path: Path):
    """A YAML file that is a scalar or list is caught as a configuration error."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("just a string")
    result = runner.invoke(app, ["validate", str(config_file)])
    assert result.exit_code == 1
    assert "Configuration Validation Error" in result.output
    assert "root: expected a mapping at top level" in result.output


def test_format_loc_helper():
    """Unit tests for format_loc helper with list indices and nested keys."""
    assert format_loc(()) == "root"
    assert format_loc(("services", "web", "image")) == "services.web.image"
    assert format_loc(("services", "web", "ports", 0)) == "services.web.ports[0]"
    assert (
        format_loc(("services", "web", "ports", 1, "extra"))
        == "services.web.ports[1].extra"
    )


def test_format_input_value_helper():
    """Unit tests for format_input_value helper."""
    assert format_input_value("three") == 'string ("three")'
    assert format_input_value(42) == "integer (42)"
    assert format_input_value(3.14) == "float (3.14)"
    assert format_input_value(True) == "boolean (true)"
    assert format_input_value(False) == "boolean (false)"
    assert format_input_value([1, 2]) == "list"
    assert format_input_value({"a": 1}) == "mapping"
    assert format_input_value(None) == "null"


def test_internal_model_construction_backward_compatibility():
    """Existing internal code/tests constructing models with extra fields must succeed."""
    # HealthCheckConfig with internal extra 'cmd'
    hc = HealthCheckConfig(
        type="process",
        cmd="dummy-process",
        interval=1,
    )
    assert hc.type == "process"
    assert hc.interval == 1

    # ServiceConfig with internal extra 'name'
    svc = ServiceConfig(
        name="api",
        image="nginx",
        restart="always",
        healthcheck=hc,
    )
    assert svc.image == "nginx"

    # DockFleetConfig with extra 'version'
    cfg = DockFleetConfig(
        version="1.0",
        services={"api": svc},
    )
    assert "api" in cfg.services


def test_model_validate_strict_vs_non_strict():
    """model_validate with extra='forbid' rejects extra fields, but regular model_validate allows them."""
    data = {
        "version": "1.0",
        "services": {
            "web": {
                "image": "nginx",
                "restart": "always",
                "extra_key": "not allowed in strict",
            }
        },
    }
    # Non-strict validation (used internally by CLI commands like 'dockfleet ps')
    cfg = DockFleetConfig.model_validate(data)
    assert "web" in cfg.services

    # Strict validation (used by 'dockfleet validate')
    with pytest.raises(ValidationError) as exc_info:
        DockFleetConfig.model_validate(data, extra="forbid")
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_load_config_strict_flag(tmp_path: Path):
    """load_config with strict=True rejects unknown fields, while strict=False ignores them."""
    config_file = tmp_path / "dockfleet.yaml"
    config_file.write_text("""
version: "1.0"
services:
  web:
    image: nginx
    restart: always
""")
    # strict=False should succeed (for backward compatibility with tools/commands)
    cfg = load_config(config_file, strict=False)
    assert "web" in cfg.services

    # strict=True (used by dockfleet validate) should reject unknown top-level 'version'
    with pytest.raises(typer.Exit) as exc_info:
        load_config(config_file, strict=True)
    assert exc_info.value.exit_code == 1
