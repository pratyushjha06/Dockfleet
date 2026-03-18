from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Union
import yaml
import re

# Healthcheck Model
class HealthCheckConfig(BaseModel):
    type: str
    endpoint: Optional[str] = None
    interval: Optional[int] = None

# Restart Policy Enum

class RestartPolicy(str, Enum):
    always = "always"
    on_failure = "on-failure"
    never = "never"

# Resources Model
class ResourcesConfig(BaseModel):
    memory: Optional[str] = None
    cpu: Optional[float] = None

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, value):
        if value is None:
            return value

        if not re.match(r"^\d+(m|g)$", value.lower()):
            raise ValueError("invalid memory limit (expected like 512m or 1g)")

        return value

    @field_validator("cpu")
    @classmethod
    def validate_cpu(cls, value):
        if value is None:
            return value

        if value <= 0:
            raise ValueError("cpu must be positive")

        return value

# Service Model
class ServiceConfig(BaseModel):
    image: str
    restart: RestartPolicy
    ports: Optional[List[str]] = None
    healthcheck: Optional[HealthCheckConfig] = None
    resources: Optional[ResourcesConfig] = None
    depends_on: Optional[List[str]] = None
    environment: Optional[Union[List[str], Dict[str, str]]] = None
    self_healing: Optional[bool] = None

    #PORT VALIDATION 
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

    #HEALTHCHECK VALIDATION 
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

    #ENV VALIDATION
    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value):
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

# Root Config Model

class DockFleetConfig(BaseModel):
    self_healing: bool = True
    services: Dict[str, ServiceConfig]
    
    @field_validator("services")
    @classmethod
    def validate_depends_on(cls, services):
        for name, svc in services.items():
            if svc.depends_on:
                for dep in svc.depends_on:
                    if dep not in services:
                        raise ValueError(
                            f"{name}: depends_on references unknown service '{dep}'"
                        )
        return services

# YAML Loader
def load_config(path: Path) -> DockFleetConfig:
    with open(path, "r") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError("Config file is empty")

    return DockFleetConfig(**data)