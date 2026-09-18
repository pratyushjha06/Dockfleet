import difflib
import re
from enum import Enum
from pathlib import Path

import typer
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


# Healthcheck Model
class HealthCheckConfig(BaseModel):
    """Configuration for service health checks (HTTP, TCP, or process)."""

    model_config = ConfigDict(extra="forbid")

    type: str
    endpoint: str | None = None
    interval: int | None = None


# Restart Policy Enum


class RestartPolicy(str, Enum):
    """Container restart policy options: always, on-failure, never."""

    always = "always"
    on_failure = "on-failure"
    never = "never"


# Resources Model
class ResourcesConfig(BaseModel):
    """Resource constraints for containers (memory and CPU limits)."""

    model_config = ConfigDict(extra="forbid")

    memory: str | None = None
    cpu: float | None = None

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, value):
        """Validate memory string format, e.g. 512m or 1g."""
        if value is None:
            return value

        if not re.match(r"^\d+(m|g)$", value.lower()):
            raise ValueError("invalid memory limit (expected like 512m or 1g)")

        return value

    @field_validator("cpu")
    @classmethod
    def validate_cpu(cls, value):
        """Validate CPU limit is a positive float."""
        if value is None:
            return value

        if value <= 0:
            raise ValueError("cpu must be positive")

        return value


# Service Model
class ServiceConfig(BaseModel):
    """Individual service specification in dockfleet.yaml."""

    model_config = ConfigDict(extra="forbid")

    image: str
    restart: RestartPolicy
    ports: list[str] | None = None
    healthcheck: HealthCheckConfig | None = None
    resources: ResourcesConfig | None = None
    depends_on: list[str] | None = None
    environment: list[str] | dict[str, str] | None = None
    self_healing: bool | None = None
    max_restarts: int | None = None
    backoff_seconds: float | None = None
    backoff_multiplier: float | None = None

    # PORT VALIDATION
    @field_validator("ports")
    @classmethod
    def validate_ports(cls, value):
        """Validate port mappings conform to host:container format."""
        if value is None:
            return value

        pattern = re.compile(r"^\d+:\d+$")

        for port in value:
            if not pattern.match(port):
                raise ValueError(
                    f"Invalid port mapping '{port}'. Expected format 'host:container'"
                )

        return value

    # HEALTHCHECK VALIDATION
    @field_validator("healthcheck")
    @classmethod
    def validate_healthcheck(cls, value):
        """Validate health check has type and interval specified."""
        if value is None:
            return value

        if value.type is None:
            raise ValueError("healthcheck.type is required")

        if value.interval is None:
            raise ValueError("healthcheck.interval is required")

        return value

    # ENV VALIDATION
    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value):
        """Validate environment variables formatted as list or dict."""
        if value is None:
            return value

        # list format → ["KEY=VALUE"]
        if isinstance(value, list):
            for item in value:
                if "=" not in item:
                    raise ValueError(
                        f"Invalid environment entry '{item}', expected KEY=VALUE"
                    )

        # dict format → {"KEY": "VALUE"}
        elif isinstance(value, dict):
            for k, v in value.items():
                if not k or not isinstance(v, str):
                    raise ValueError("Invalid environment dict format")

        return value

    @field_validator("max_restarts")
    @classmethod
    def validate_max_restarts(cls, value):
        """Validate maximum self-healing restart attempts."""
        if value is not None and value <= 0:
            raise ValueError("max_restarts must be greater than 0")
        return value

    @field_validator("backoff_seconds")
    @classmethod
    def validate_backoff_seconds(cls, value):
        """Validate initial restart backoff."""
        if value is not None and value < 0:
            raise ValueError("backoff_seconds must be non-negative")
        return value

    @field_validator("backoff_multiplier")
    @classmethod
    def validate_backoff_multiplier(cls, value):
        """Validate exponential backoff multiplier."""
        if value is not None and value < 1:
            raise ValueError("backoff_multiplier must be at least 1")
        return value

# Root Config Model

class DockFleetConfig(BaseModel):
    """Top-level Dockfleet deployment configuration model."""

    model_config = ConfigDict(extra="forbid")

    self_healing: bool = True
    services: dict[str, ServiceConfig]

    @field_validator("services")
    @classmethod
    def validate_depends_on(cls, services):
        """Validate dependency references point to existing services."""
        for name, svc in services.items():
            if svc.depends_on:
                for dep in svc.depends_on:
                    if dep not in services:
                        raise ValueError(
                            f"{name}: depends_on references unknown service '{dep}'"
                        )
        return services


# ---------------------------------------------------------------------------
# Friendly, field-specific error formatting
# ---------------------------------------------------------------------------
#
# Pydantic's raw ValidationError already tells us *where* a problem is
# (via `loc`) and *what* pydantic thinks is wrong (via `type`/`msg`), but the
# raw messages are aimed at library authors, not end users editing a YAML
# file. The helpers below translate those raw errors into lines that name
# the exact service + field, say what was expected vs. what was given, and
# suggest a fix for typo'd/unknown keys.

# Fields known to each model, used to build "did you mean" suggestions and
# to identify which model a nested field belongs to.
_ROOT_FIELDS = list(DockFleetConfig.model_fields.keys())
_SERVICE_FIELDS = list(ServiceConfig.model_fields.keys())
_HEALTHCHECK_FIELDS = list(HealthCheckConfig.model_fields.keys())
_RESOURCES_FIELDS = list(ResourcesConfig.model_fields.keys())

_FIELDS_BY_KIND: dict[str, list[str]] = {
    "root": _ROOT_FIELDS,
    "service": _SERVICE_FIELDS,
    "healthcheck": _HEALTHCHECK_FIELDS,
    "resources": _RESOURCES_FIELDS,
}

# Human descriptions for pydantic's "wrong shape" error types. Errors of
# these types are merged when several show up for the same field (this
# happens for union-typed fields like `environment: list[str] | dict[str, str]`,
# where an invalid input produces one raw error per union branch).
_TYPE_MISMATCH_HINTS: dict[str, str] = {
    "string_type": "a text value",
    "int_type": "a whole number",
    "int_parsing": "a whole number",
    "float_type": "a number",
    "float_parsing": "a number",
    "bool_type": "a true/false value",
    "bool_parsing": "a true/false value",
    "list_type": "a list",
    "dict_type": "a mapping of key: value pairs",
}

# A synthetic path segment pydantic inserts for union branches, e.g.
# ('services', 'api', 'environment', 'list[str]'). Not a real field name.
_UNION_BRANCH_RE = re.compile(r"^(list|dict|set|tuple)\[")


def _describe_location(loc: tuple) -> tuple[str, str, str]:
    """
    Split a pydantic error `loc` tuple into:
      context    - human-readable "where", e.g. "service 'api'" or "config"
      field_path - dotted field path relative to that context, e.g.
                   "healthcheck.interval"
      field_kind - which model the *final* field belongs to, used to look
                   up valid field names for suggestions ("root", "service",
                   "healthcheck", or "resources")
    """
    parts = [p for p in loc if not _UNION_BRANCH_RE.match(str(p))]

    if not parts:
        return "config", "", "root"

    if parts[0] != "services":
        return "config", ".".join(str(p) for p in parts), "root"

    if len(parts) == 1:
        # The `services` key itself is missing or has the wrong shape.
        return "config", "services", "root"

    service_name = parts[1]
    context = f"service '{service_name}'"
    rest = parts[2:]

    if not rest:
        return context, "", "service"

    field_kind = "service"
    if rest[0] == "healthcheck":
        field_kind = "healthcheck"
    elif rest[0] == "resources":
        field_kind = "resources"

    return context, ".".join(str(p) for p in rest), field_kind


def _suggest_field(field_name: str, field_kind: str) -> str | None:
    """Suggest the closest known field name for a likely typo, if any."""
    candidates = _FIELDS_BY_KIND.get(field_kind, [])
    matches = difflib.get_close_matches(field_name, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _format_single_error(err: dict) -> str:
    """Format one non-mergeable pydantic error into a human-readable line."""
    context, field_path, field_kind = _describe_location(err["loc"])
    err_type = err["type"]

    if err_type == "missing":
        return f"{context}: missing required field '{field_path}'"

    if err_type == "extra_forbidden":
        field_name = field_path.rsplit(".", 1)[-1]
        suggestion = _suggest_field(field_name, field_kind)
        hint = f" (did you mean '{suggestion}'?)" if suggestion else ""
        return f"{context}: unknown field '{field_path}'{hint}"

    if err_type == "enum":
        expected = err.get("ctx", {}).get("expected", "a valid value")
        given = err.get("input")
        return (
            f"{context}: field '{field_path}' has invalid value {given!r} "
            f"(expected one of: {expected})"
        )

    if err_type == "value_error":
        # Raised by our own @field_validator methods - the message is
        # already written for humans, just attach the exact location.
        msg = err["msg"]
        prefix = "Value error, "
        if msg.startswith(prefix):
            msg = msg[len(prefix) :]
        return f"{context}: field '{field_path}' — {msg}"

    # Fallback for anything not explicitly handled above: still names the
    # exact service/field rather than dumping a bare pydantic error.
    return f"{context}: field '{field_path}' — {err['msg']}"


def format_validation_error(exc: ValidationError) -> list[str]:
    """
    Convert a pydantic ValidationError into de-duplicated, human-readable
    lines that each name the offending service and field.
    """
    merged: dict[tuple[str, str], dict] = {}
    merged_order: list[tuple[str, str]] = []
    other_lines: list[str] = []

    for err in exc.errors():
        err_type = err["type"]

        if err_type in _TYPE_MISMATCH_HINTS:
            context, field_path, _kind = _describe_location(err["loc"])
            key = (context, field_path)
            if key not in merged:
                merged[key] = {"given": err.get("input"), "expected": []}
                merged_order.append(key)
            hint = _TYPE_MISMATCH_HINTS[err_type]
            if hint not in merged[key]["expected"]:
                merged[key]["expected"].append(hint)
            continue

        other_lines.append(_format_single_error(err))

    merged_lines = []
    for key in merged_order:
        context, field_path = key
        info = merged[key]
        expected = " or ".join(info["expected"])
        merged_lines.append(
            f"{context}: field '{field_path}' expected {expected}, "
            f"got {info['given']!r}"
        )

    return merged_lines + other_lines


# YAML Loader
def load_config(path: Path) -> DockFleetConfig:
    """Parse and validate YAML configuration file into a DockFleetConfig object."""
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if not data:
            typer.echo(f"Error: Config file '{path}' is empty.", err=True)
            raise typer.Exit(code=1)

        return DockFleetConfig(**data)
    except yaml.YAMLError as e:
        typer.echo(f"Error parsing YAML file '{path}':\n{e}", err=True)
        raise typer.Exit(code=1)
    except ValidationError as e:
        typer.echo(f"Configuration Validation Error in '{path}':", err=True)
        for line in format_validation_error(e):
            typer.echo(f" - {line}", err=True)
        raise typer.Exit(code=1)
    except FileNotFoundError:
        typer.echo(f"Error: Configuration file '{path}' not found.", err=True)
        raise typer.Exit(code=1)
