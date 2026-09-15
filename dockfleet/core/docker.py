import subprocess


class DockerManager:
    """Wrapper around Docker CLI subcommands."""

    def create_network(self, name: str):
        """Create a user-defined bridge network if not already present."""
        try:
            subprocess.run(
                ["docker", "network", "create", name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            # Docker reports an existing network as a command failure even
            # though this operation is intentionally idempotent. Suppress only
            # that expected case; daemon, permission, validation, and other
            # failures must propagate so startup does not continue on a network
            # that was never created.
            stderr = exc.stderr or ""
            if "already exists" in stderr.lower():
                return
            raise

    def run_container(self, image, name, flags=None, network=None):
        """Run a container in detached mode with optional flags and network."""
        cmd = ["docker", "run", "-d", "--name", name]

        if network:
            cmd += ["--network", network]

        if flags:
            cmd += flags

        cmd.append(image)

        subprocess.run(cmd, check=True)

    def remove_container(self, name):
        """Forcefully remove a container by name."""
        result = subprocess.run(
            ["docker", "rm", "-f", name], capture_output=True, text=True
        )

        if result.returncode != 0:
            if "No such container" in result.stderr:
                return
            else:
                raise RuntimeError(result.stderr)

    def stop_container(self, name):
        """Stop a running container by name."""
        subprocess.run(["docker", "stop", name], check=True)

    def list_containers(self):
        """Print currently running Docker containers."""
        subprocess.run(["docker", "ps"], check=True)
