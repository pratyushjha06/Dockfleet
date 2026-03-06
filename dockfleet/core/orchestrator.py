from dockfleet.core.docker import DockerManager
from dockfleet.health.status import (
    mark_service_running,
    mark_service_stopped
)


class Orchestrator:

    def __init__(self, config):
        self.config = config
        self.docker = DockerManager()
        self.network = "dockfleet_net"

    def up(self):

        print("Starting services...\n")

        self.docker.create_network(self.network)

        for name, svc in self.config.services.items():

            container_name = f"dockfleet_{name}"
            ports = svc.ports or []

            try:
                # remove old container if it exists
                self.docker.remove_container(container_name)

                # start container
                self.docker.run_container(
                    image=svc.image,
                    name=container_name,
                    ports=ports,
                    network=self.network
                )

                mark_service_running(name)

                print(f"✓ Started service: {name}")

            except Exception as e:

                print(f"✗ Failed to start {name}")
                print(e)

    def down(self):

        print("Stopping services...\n")

        for name in self.config.services.keys():

            container_name = f"dockfleet_{name}"

            try:
                # clean shutdown
                self.docker.stop_container(container_name)

                # remove container
                self.docker.remove_container(container_name)

                mark_service_stopped(name)

                print(f"✓ Stopped service: {name}")

            except Exception as e:

                print(f"✗ Failed to stop {name}")
                print(e)

    def ps(self):

        print("Running containers:\n")

        self.docker.list_containers()