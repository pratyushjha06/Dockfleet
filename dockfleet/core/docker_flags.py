def build_resource_flags(service_config: dict) -> list[str]:

    flags = []

    memory = service_config.get("memory")
    cpus = service_config.get("cpus")

    if memory:
        flags.extend(["--memory", str(memory)])

    if cpus:
        flags.extend(["--cpus", str(cpus)])

    return flags


def build_env_flags(config):
    flags = []
    env = config.get("env") or {}

    if isinstance(env, list):
        env_dict = {}
        for item in env:
            if "=" in item:
                k, v = item.split("=", 1)
                env_dict[k] = v
        env = env_dict

    for key, value in env.items():
        flags.extend(["-e", f"{key}={value}"])

    return flags


def build_port_flags(config):
    flags = []
    ports = config.get("ports") or []

    if isinstance(ports, dict):
        ports = [f"{k}:{v}" for k, v in ports.items()]

    for port in ports:
        flags.extend(["-p", port])

    return flags