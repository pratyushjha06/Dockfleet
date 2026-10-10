import re
from enum import Enum
from pathlib import Path

import typer
import yaml
from pydantic import BaseModel, ValidationError, field_validator


# Healthcheck Model
class HealthCheckConfig(BaseModel):
    """Configuration for service health checks (HTTP, TCP, or process)."""

    type: str
    endpoint: str | None = None
    interval: int | None = None

    @field_validator("interval")
    @classmethod
    def validate_interval(cls, value):
        """Validate healthcheck interval is a positive integer."""
        if value is not None and value <= 0:
            raise ValueError("interval must be greater than 0")
        return value


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
        """Validate memory string format, e.g. 512m, 512mb, 1g, 1gb."""
        if value is None:
            return value

        if not re.match(r"^\d+(b|k|m|g|t|kb|mb|gb|tb|kib|mib|gib|tib)$", value.lower()):
            raise ValueError("invalid memory limit (expected like 512m, 512mb, 1g, 1gb)")

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
    ports: list[str] | dict[str, str | int] | None = None
    healthcheck: HealthCheckConfig | None = None
    resources: ResourcesConfig | None = None
    depends_on: list[str] | None = None
    environment: list[str] | dict[str, str] | None = None
    self_healing: bool | None = None
    max_restarts: int | None = None
    backoff_seconds: float | None = None
    backoff_multiplier: float | None = None

    # PORT VALIDATION
    @field_validator("ports", mode="before")
    @classmethod
    def validate_ports(cls, value):
        """Validate port mappings conform to [host_ip:]host:container[/protocol] format, valid port range (1-65535), and dict or list format."""
        if value is None:
            return value

        # Convert dictionary format to list of strings
        if isinstance(value, dict):
            converted = []
            for k, v in value.items():
                if not k or not isinstance(v, (str, int)):
                    raise ValueError(f"Invalid port mapping dict format: {k}: {v}")
                converted.append(f"{k}:{v}")
            value = converted

        if not isinstance(value, list):
            raise ValueError("Invalid ports format, expected list or dict")

        normalized = []
        for port in value:
            port_str = str(port).strip()
            if not port_str:
                raise ValueError("Invalid port mapping ''. Expected format 'host:container'")

            clean_port = port_str
            if "/" in clean_port:
                parts_proto = clean_port.split("/")
                if len(parts_proto) != 2 or not parts_proto[1]:
                    raise ValueError(
                        f"Invalid port mapping '{port_str}'. Expected format 'host:container'"
                    )
                proto = parts_proto[1].lower()
                if proto not in {"tcp", "udp", "sctp"}:
                    raise ValueError(
                        f"Invalid port protocol '{proto}' in '{port_str}'. Expected tcp, udp, or sctp"
                    )
                clean_port = parts_proto[0]

            parts = clean_port.split(":")
            if len(parts) == 2:
                host_str, container_str = parts
            elif len(parts) == 3:
                host_ip, host_str, container_str = parts
                if not host_ip:
                    raise ValueError(
                        f"Invalid port mapping '{port_str}'. Expected format 'host:container'"
                    )
            else:
                raise ValueError(
                    f"Invalid port mapping '{port_str}'. Expected format 'host:container'"
                )

            if not host_str.isdigit() or not container_str.isdigit():
                raise ValueError(
                    f"Invalid port mapping '{port_str}'. Expected format 'host:container'"
                )

            host_port = int(host_str)
            container_port = int(container_str)

            if not (1 <= host_port <= 65535 and 1 <= container_port <= 65535):
                raise ValueError(
                    f"Invalid port mapping '{port_str}'. Port values must be between 1 and 65535"
                )

            normalized.append(port_str)

        return normalized

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

        if value.interval <= 0:
            raise ValueError("healthcheck.interval must be greater than 0")

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
        for err in e.errors():
            loc = " -> ".join(str(location_part) for location_part in err["loc"])
            msg = err["msg"]
            typer.echo(f" - {loc}: {msg}", err=True)
        raise typer.Exit(code=1)
    except FileNotFoundError:
        typer.echo(f"Error: Configuration file '{path}' not found.", err=True)
        raise typer.Exit(code=1)
