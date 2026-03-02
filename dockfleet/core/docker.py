class DockerManager:
    def __init__(self, ssh):
        self.ssh = ssh

    def create_network(self, name):
        self.ssh.run(f"docker network create {name}")

    def build_image(self, image_name, path):
        self.ssh.run(f"docker build -t {image_name} {path}")

    def run_container(self, image, name, port, network):
        return self.ssh.run(
            f"docker run -d --name {name} "
            f"--network {network} "
            f"-p {port}:{port} {image}"
        )

    def stop_container(self, name):
        self.ssh.run(f"docker stop {name}")