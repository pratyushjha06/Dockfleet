from dockfleet.core.docker import DockerManager
from dockfleet.core.ssh import SSHClient
from dockfleet.core.state import StateManager


class Orchestrator:
    def __init__(
        self,
        app=None,
        docker_adapter=None,
        ssh_client=None,
        state_manager=None,
    ):
        self.app = app

        # Dependency Injection (with fallback defaults)
        self.ssh = ssh_client or (SSHClient(app.vps) if app else None)
        self.docker = docker_adapter or (
            DockerManager(self.ssh) if self.ssh else None
        )
        self.state = state_manager or (
            StateManager(app.name) if app else None
        )

    def deploy(self):
        if not self.app:
            raise ValueError("App configuration required for deploy")

        network = f"{self.app.name}_net"
        self.docker.create_network(network)

        services_state = {}

        for service in self.app.services:
            image = f"{self.app.name}_{service.name}"
            container = image

            self.docker.build_image(image, service.path)

            container_id = self.docker.run_container(
                image=image,
                container_name=container,
                port=service.port,
                network=network,
            )

            services_state[service.name] = {
                "container_name": container,
                "container_id": container_id,
                "port": service.port,
                "status": "running",
            }

        self.state.save(
            {
                "app": self.app.name,
                "vps": self.app.vps,
                "network": network,
                "services": services_state,
            }
        )
    def generate_plan(self, desired, current_state):

        plan = {
            "create": [],
            "remove": [],
            "update": [],
        }

        desired_services = desired.get("services", {})
        current_services = current_state.get("services", {})

        # Services to create
        for name, config in desired_services.items():
            if name not in current_services:
                plan["create"].append({
                    "name": name,
                    "image": config["image"]
                })

        # Services to remove
        for name in current_services:
            if name not in desired_services:
                plan["remove"].append(name)

        return plan