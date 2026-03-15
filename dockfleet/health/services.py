import json
from sqlmodel import Session, select
from .models import Service
from dockfleet.cli.config import (
    DockFleetConfig,
    ServiceConfig,
    HealthCheckConfig,
    RestartPolicy,
)

def services_from_config(config: DockFleetConfig) -> list[Service]:
    services: list[Service] = []

    # config.services: Dict[str, ServiceConfig]
    for name, svc_cfg in config.services.items():
        image = svc_cfg.image
        restart_policy = svc_cfg.restart.value  # always / on-failure / never

        # 2) Ports → ports_raw (string or None)
        if svc_cfg.ports is None:
            ports_raw = None
        else:
            ports_raw = ",".join(svc_cfg.ports)

        # 3) Healthcheck → healthcheck_raw (JSON string or None)
        if svc_cfg.healthcheck is None:
            healthcheck_raw = None
        else:
            hc = svc_cfg.healthcheck
            hc_dict = {
                "type": hc.type,
                "endpoint": hc.endpoint,
                "interval": hc.interval,
            }
            healthcheck_raw = json.dumps(hc_dict)

        # 4) Resources → resources_memory / resources_cpu (optional)
        if svc_cfg.resources is None:
            resources_memory = None
            resources_cpu = None
        else:
            resources_memory = svc_cfg.resources.memory
            resources_cpu = svc_cfg.resources.cpu

        # 5) Environment → env_raw (JSON string or None)
        # ServiceConfig.environment: Optional[List[str]]
        if svc_cfg.environment is None:
            env_raw = None
        else:
            # store as JSON array of strings for future parsing
            env_raw = json.dumps(svc_cfg.environment)

        # 6) Depends_on → depends_on_raw (comma-separated or None)
        if svc_cfg.depends_on is None:
            depends_on_raw = None
        else:
            depends_on_raw = ",".join(svc_cfg.depends_on)

        # 7) Runtime defaults (not from config)
        status = "stopped"
        restart_count = 0
        last_health_check = None
        last_failure_reason = None
        consecutive_failures = 0

        # 8) Create Service instance not in DB
        service = Service(
            name=name,
            image=image,
            restart_policy=restart_policy,
            ports_raw=ports_raw,
            healthcheck_raw=healthcheck_raw,
            status=status,
            restart_count=restart_count,
            last_health_check=last_health_check,
            last_failure_reason=last_failure_reason,
            consecutive_failures=consecutive_failures,
            resources_memory=resources_memory,
            resources_cpu=resources_cpu,
            env_raw=env_raw,
            depends_on_raw=depends_on_raw,
        )

        services.append(service)

    return services

def seed_services(config: DockFleetConfig, session: Session) -> None:
    services = services_from_config(config)

    for svc in services:
        # Check if a service with this name already exists
        existing = session.exec(
            select(Service).where(Service.name == svc.name)
        ).one_or_none()

        if existing is not None:
            # Already present -> skip
            continue

        # Not present -> add new row
        session.add(svc)

    session.commit()