import yaml
from dockfleet.core.models import App, Service

def load_config(path="sidectl.yaml"):
    with open(path) as f:
        data = yaml.safe_load(f)

    services = [
        Service(name=k, path=v["path"], port=v["port"])
        for k, v in data["services"].items()
    ]

    return App(
        name=data["app"],
        vps=data["vps"],
        services=services
    )