from dockfleet.core.docker import DockerManager
from dockfleet.core.ssh import SSHClient
from dockfleet.core.state import StateManager
from dockfleet.core.plan import Plan

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

    def up(self):
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

        to_create = []
        to_remove = []
        to_update = []

        desired_services = desired.get("services", {})
        current_services = current_state.get("services", {})

    # Services to create
        for name, config in desired_services.items():
            if name not in current_services:
                to_create.append({
                    "name": name,
                    "image": config["image"]
                })

    # Services to remove
        for name in current_services:
            if name not in desired_services:
                to_remove.append(name)

        for name, config in desired_services.items():
            if name in current_services:
                if config["image"] != current_services[name]["image"]:
                    to_update.append({
                        "name": name,
                        "image": config["image"]
                    })

        return Plan(
            to_create=to_create,
            to_remove=to_remove,
            to_update=to_update
            )
    
    def down(self):
        if not self.app:
            raise ValueError("App configuration required")

        print(f"\n[INFO] Shutting down app: {self.app.name}")

        state = self.state.load()

        if not state:
            print("[INFO] No state found. Nothing to stop.")
            return

        services = state.get("services", {})

        for name, data in services.items():
            container = data["container_name"]

            print(f"[INFO] Stopping container: {container}")
            self.docker.stop_container(container)

            print(f"[INFO] Removing container: {container}")
            self.docker.remove_container(container)

        network = state.get("network")

        if network:
            print(f"[INFO] Removing network: {network}")
            self.docker.remove_network(network)

        print("\n[INFO] Shutdown complete")

    def ps(self):
        app_name = self.app.name

        output = self.ssh.run(
            f'docker ps -a --filter name={app_name} '
            f'--format "{{{{.Names}}}}|{{{{.Image}}}}|{{{{.Status}}}}"'
        )

        GREEN = "\033[92m"
        RED = "\033[91m"
        WHITE = "\033[97m"
        RESET = "\033[0m"

        print("\nSERVICE\t\tIMAGE\t\tSTATUS")

        for line in output.splitlines():
            if "|" not in line:
                continue

            name, image, status = line.split("|")

            if "Up" in status:
                color = GREEN
            elif "Exited" in status:
                color = RED
            else:
                color = WHITE

            print(f"{name}\t{image}\t{color}{status}{RESET}")
        
        return output