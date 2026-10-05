import difflib
import re
from enum import Enum
from pathlib import Path
from typing import Any

import typer
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


# Healthcheck Model
class HealthCheckConfig(BaseModel):
    """Configuration for service health checks (HTTP, TCP, or process)."""

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
        """Validate port mappings conform to host:container format and valid port range (1-65535)."""
        if value is None:
            return value

        pattern = re.compile(r"^\d+:\d+$")

        for port in value:
            if not pattern.match(port):
                raise ValueError(
                    f"Invalid port mapping '{port}'. Expected format 'host:container'"
                )

            host_str, container_str = port.split(":")
            host_port = int(host_str)
            container_port = int(container_str)

            if not (1 <= host_port <= 65535 and 1 <= container_port <= 65535):
                raise ValueError(
                    f"Invalid port mapping '{port}'. Port values must be between 1 and 65535"
                )

        return value

    # HEALTHCHECK VALIDATION
    @field_validator("healthcheck")
    @classmethod
    def validate_healthcheck(cls, value):
        """Validate health check has type and interval specified, and endpoint when required."""
        if value is None:
            return value

        if value.type is None:
            raise ValueError("healthcheck.type is required")

        if value.interval is None:
            raise ValueError("healthcheck.interval is required")

        if value.type.lower() in {"http", "tcp"} and not value.endpoint:
            raise ValueError(
                f"healthcheck.endpoint is required when type is '{value.type}'"
            )

        return value

    # ENV VALIDATION
    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, value):
        """Validate environment variables formatted as list or dict, converting scalar values to strings."""
        if value is None:
            return value

        # list format → ["KEY=VALUE"]
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, str) or "=" not in item:
                    raise ValueError(
                        f"Invalid environment entry '{item}', expected KEY=VALUE"
                    )
            return value

        # dict format → {"KEY": "VALUE"}
        elif isinstance(value, dict):
            converted = {}
            for k, v in value.items():
                if not k or not isinstance(v, (str, int, float, bool)):
                    raise ValueError("Invalid environment dict format")
                converted[str(k)] = str(v)
            return converted

        raise ValueError("Invalid environment format, expected list or dict")

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

    self_healing: bool = True
    services: dict[str, ServiceConfig]

    @field_validator("services")
    @classmethod
    def validate_depends_on(cls, services):
        """Validate dependency references and reject circular service graphs."""
        for name, svc in services.items():
            if svc.depends_on:
                for dep in svc.depends_on:
                    if dep not in services:
                        raise ValueError(
                            f"{name}: depends_on references unknown service '{dep}'"
                        )

        visited: set[str] = set()
        visiting: set[str] = set()
        path: list[str] = []

        def visit(name: str) -> None:
            if name in visiting:
                cycle_start = path.index(name)
                cycle = path[cycle_start:] + [name]
                raise ValueError(
                    "circular depends_on relationship: " + " -> ".join(cycle)
                )

            if name in visited:
                return

            visiting.add(name)
            path.append(name)
            for dependency in services[name].depends_on or []:
                visit(dependency)
            path.pop()
            visiting.remove(name)
            visited.add(name)

        for name in services:
            visit(name)
        return services

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        extra: str | None = None,
        **kwargs: Any,
    ) -> "DockFleetConfig":
        """Validate input data with optional extra='forbid' override."""
        if extra == "forbid":
            return StrictDockFleetConfig.model_validate(
                obj,
                strict=strict,
                from_attributes=from_attributes,
                context=context,
                **kwargs,
            )
        return super().model_validate(
            obj,
            strict=strict,
            from_attributes=from_attributes,
            context=context,
            **kwargs,
        )


# Strict models used by strict validation (e.g. dockfleet validate)
class StrictHealthCheckConfig(HealthCheckConfig):
    model_config = ConfigDict(extra="forbid")


class StrictResourcesConfig(ResourcesConfig):
    model_config = ConfigDict(extra="forbid")


class StrictServiceConfig(ServiceConfig):
    model_config = ConfigDict(extra="forbid")

    healthcheck: StrictHealthCheckConfig | None = None
    resources: StrictResourcesConfig | None = None


class StrictDockFleetConfig(DockFleetConfig):
    model_config = ConfigDict(extra="forbid")

    services: dict[str, StrictServiceConfig]


def format_loc(loc: tuple[int | str, ...] | list[int | str]) -> str:
    """Format a Pydantic error loc tuple into human-readable dot/bracket path."""
    if not loc:
        return "root"
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            if path:
                path += f".{part}"
            else:
                path = str(part)
    return path


def format_input_value(val: Any) -> str:
    """Format actual input value into an actionable string description."""
    if isinstance(val, bool):
        return f"boolean ({str(val).lower()})"
    elif isinstance(val, int):
        return f"integer ({val})"
    elif isinstance(val, float):
        return f"float ({val})"
    elif isinstance(val, str):
        display_val = val if len(val) <= 40 else val[:37] + "..."
        return f'string ("{display_val}")'
    elif isinstance(val, list):
        return "list"
    elif isinstance(val, dict):
        return "mapping"
    elif val is None:
        return "null"
    return type(val).__name__


def get_allowed_fields_for_loc(
    loc: tuple[int | str, ...] | list[int | str],
) -> list[str]:
    """Retrieve the list of allowed fields for the parent model of an unknown field."""
    parent_loc = tuple(loc[:-1])
    if len(parent_loc) == 0:
        return list(DockFleetConfig.model_fields.keys())
    if len(parent_loc) == 2 and parent_loc[0] == "services":
        return list(ServiceConfig.model_fields.keys())
    if len(parent_loc) == 3 and parent_loc[0] == "services":
        if parent_loc[2] == "healthcheck":
            return list(HealthCheckConfig.model_fields.keys())
        if parent_loc[2] == "resources":
            return list(ResourcesConfig.model_fields.keys())
    return []


def format_validation_error(err: dict[str, Any]) -> str:
    """Format a single Pydantic error dictionary into an actionable error line."""
    loc = format_loc(err.get("loc", ()))
    error_type = err.get("type", "")
    input_val = err.get("input")
    msg = err.get("msg", "")

    # 1. Missing required field
    if error_type == "missing":
        return f"{loc}: required field is missing"

    # 2. Unknown / misspelled field (extra inputs)
    if error_type == "extra_forbidden":
        offending_field = err.get("loc", ())[-1] if err.get("loc") else ""
        allowed = get_allowed_fields_for_loc(err.get("loc", ()))
        suggestion = ""
        if offending_field and allowed:
            matches = difflib.get_close_matches(
                str(offending_field), allowed, n=1, cutoff=0.6
            )
            if matches:
                suggestion = f'. Did you mean "{matches[0]}"?'
        return f"{loc}: unknown field{suggestion}"

    # 3. Invalid enum or literal value
    if error_type in ("enum", "literal_error"):
        ctx = err.get("ctx", {})
        expected = ctx.get("expected")
        if expected:
            cleaned = str(expected).replace(" or ", ", ").replace("'", "")
            expected_clause = f"; expected one of: {cleaned}"
        else:
            expected_clause = ""
        display_input = f'"{input_val}"' if isinstance(input_val, str) else str(input_val)
        return f"{loc}: invalid value {display_input}{expected_clause}"

    # 4. Wrong field types
    if error_type in ("int_parsing", "int_type", "int_from_float"):
        return f"{loc}: expected an integer, got {format_input_value(input_val)}"
    if error_type in ("float_parsing", "float_type"):
        return f"{loc}: expected a number, got {format_input_value(input_val)}"
    if error_type in ("bool_parsing", "bool_type"):
        return f"{loc}: expected a boolean, got {format_input_value(input_val)}"
    if error_type in ("string_type",):
        return f"{loc}: expected a string, got {format_input_value(input_val)}"
    if error_type in ("list_type",):
        return f"{loc}: expected a list, got {format_input_value(input_val)}"
    if error_type in ("dict_type", "model_type"):
        return f"{loc}: expected a mapping, got {format_input_value(input_val)}"

    # 5. Value error (from custom validators)
    if error_type == "value_error":
        clean_msg = msg
        if clean_msg.startswith("Value error, "):
            clean_msg = clean_msg[len("Value error, "):]
        return f"{loc}: {clean_msg}"

    # 6. Comparisons
    if error_type == "greater_than":
        limit = err.get("ctx", {}).get("gt")
        return f"{loc}: value must be greater than {limit}"
    if error_type == "greater_than_equal":
        limit = err.get("ctx", {}).get("ge")
        return f"{loc}: value must be at least {limit}"
    if error_type == "less_than":
        limit = err.get("ctx", {}).get("lt")
        return f"{loc}: value must be less than {limit}"
    if error_type == "less_than_equal":
        limit = err.get("ctx", {}).get("le")
        return f"{loc}: value must be at most {limit}"

    clean_msg = msg
    if clean_msg.startswith("Value error, "):
        clean_msg = clean_msg[len("Value error, "):]
    return f"{loc}: {clean_msg}"


def format_validation_errors(e: ValidationError) -> list[str]:
    """Format all validation errors from a ValidationError into deduplicated actionable lines."""
    formatted: list[str] = []
    seen: set[str] = set()
    for err in e.errors():
        formatted_error = format_validation_error(err)
        if formatted_error not in seen:
            seen.add(formatted_error)
            formatted.append(formatted_error)
    return formatted


# YAML Loader
def load_config(path: Path | str, strict: bool = False) -> DockFleetConfig:
    """Parse and validate YAML configuration file into a DockFleetConfig object."""
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if not data:
            typer.echo(f"Error: Config file '{path}' is empty.", err=True)
            raise typer.Exit(code=1)

        if not isinstance(data, dict):
            typer.echo(f"Configuration Validation Error in '{path}':", err=True)
            typer.echo("Validation failed:", err=True)
            typer.echo("  root: expected a mapping at top level", err=True)
            raise typer.Exit(code=1)

        if strict:
            return DockFleetConfig.model_validate(data, extra="forbid")
        return DockFleetConfig.model_validate(data)
    except yaml.YAMLError as e:
        typer.echo(f"Invalid YAML syntax in '{path}':\n{e}", err=True)
        raise typer.Exit(code=1)
    except ValidationError as e:
        typer.echo(f"Configuration Validation Error in '{path}':", err=True)
        typer.echo("Validation failed:", err=True)
        for err_msg in format_validation_errors(e):
            typer.echo(f"  {err_msg}", err=True)
        raise typer.Exit(code=1)
    except FileNotFoundError:
        typer.echo(f"Error: Configuration file '{path}' not found.", err=True)
        raise typer.Exit(code=1)
