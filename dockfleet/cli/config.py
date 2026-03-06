from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict
import yaml
import re

# healthcheck model
class HealthCheckConfig(BaseModel):
    type: str
    endpoint: Optional[str] = None
    interval: Optional[int] = None

# Restart policy Enum
class RestartPolicy(str, Enum):
    always = "always"
    on_failure = "on-failure"
    never = "never"

# Resource limits model
class ResourcesConfig(BaseModel):
    memory: Optional[str] = None
    cpu: Optional[float] = None

# service model
class ServiceConfig(BaseModel):
    image: str
    restart: RestartPolicy
    ports: Optional[List[str]] = None
    healthcheck: Optional[HealthCheckConfig] = None
    resources: Optional[ResourcesConfig] = None
    depends_on: Optional[List[str]] = None
    environment: Optional[List[str]] = None

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, value):
        if value is None:
            return value

        pattern = re.compile(r"^\d+:\d+$")

        for port in value:
            if not pattern.match(port):
                raise ValueError(
                    f"Invalid port mapping '{port}'. Expected format 'host:container'"
                )

        return value

    @field_validator("healthcheck")
    @classmethod
    def validate_healthcheck(cls, value):
        if value is None:
            return value

        if value.type is None:
            raise ValueError("healthcheck.type is required")

        if value.interval is None:
            raise ValueError("healthcheck.interval is required")

        return value

# Root Config Model
class DockFleetConfig(BaseModel):
    services: Dict[str, ServiceConfig]

# YAML loader
def load_config(path: Path) -> DockFleetConfig:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if not data:
        raise ValueError("Config file is empty")

    return DockFleetConfig(**data)