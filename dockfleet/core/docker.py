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
        """Stop a running container by name.

        Idempotent: a container that no longer exists is treated as already
        stopped. Other Docker failures raise ``RuntimeError`` with the CLI's
        stderr attached so callers see the actual cause (daemon unreachable,
        permission denied, ...) instead of a bare exit status.
        """
        try:
            result = subprocess.run(
                ["docker", "stop", name],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"docker stop {name} timed out after 60 seconds"
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "no such container" in stderr.lower():
                return
            raise RuntimeError(
                stderr
                or f"docker stop {name} failed with exit code {result.returncode}"
            )

    def list_containers(self):
        """Print currently running Docker containers."""
        subprocess.run(["docker", "ps"], check=True)

    def get_containers_json(self) -> list:
        import json

        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        containers = []
        for line in result.stdout.splitlines():
            if line.strip():
                try:
                    containers.append(json.loads(line.strip()))
                except Exception:
                    pass
        return containers
