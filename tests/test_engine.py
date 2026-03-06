from dockfleet.core.orchestrator import Orchestrator


def test_create_when_empty_state():
    desired = {
        "services": {
            "nginx": {"image": "nginx:latest"}
        }
    }

    state = {}

    orchestrator = Orchestrator()
    plan = orchestrator.generate_plan(desired, state)

    assert len(plan.to_create) == 1
    assert plan.to_create[0]["name"] == "nginx"
    assert len(plan.to_remove) == 0

def test_update_when_image_changes():
    desired = {
        "services": {
            "nginx": {"image": "nginx:1.25"}
        }
    }

    state = {
        "services": {
            "nginx": {"image": "nginx:latest"}
        }
    }

    orchestrator = Orchestrator()
    plan = orchestrator.generate_plan(desired, state)

    assert len(plan.to_update) == 1

def test_no_changes_when_states_match():
    desired = {
        "services": {
            "nginx": {"image": "nginx:latest"}
        }
    }

    state = {
        "services": {
            "nginx": {"image": "nginx:latest"}
        }
    }

    orchestrator = Orchestrator()
    plan = orchestrator.generate_plan(desired, state)

    assert plan.to_create == []
    assert plan.to_remove == []
    assert plan.to_update == []

class FakeDocker:
    def __init__(self):
        self.stopped = []
        self.removed = []
        self.network_removed = None

    def stop_container(self, name):
        self.stopped.append(name)

    def remove_container(self, name):
        self.removed.append(name)

    def remove_network(self, name):
        self.network_removed = name


class FakeState:
    def load(self):
        return {
            "app": "testapp",
            "network": "testapp_net",
            "services": {
                "web": {
                    "container_name": "testapp_web"
                },
                "api": {
                    "container_name": "testapp_api"
                }
            }
        }


class FakeApp:
    name = "testapp"
    vps = "dummy"


def test_down_stops_and_removes_containers():
    docker = FakeDocker()
    state = FakeState()

    orchestrator = Orchestrator(
        app=FakeApp(),
        docker_adapter=docker,
        state_manager=state
    )

    orchestrator.down()

    assert "testapp_web" in docker.stopped
    assert "testapp_api" in docker.stopped

    assert "testapp_web" in docker.removed
    assert "testapp_api" in docker.removed

    assert docker.network_removed == "testapp_net"

def test_down_when_no_state():
    class EmptyState:
        def load(self):
            return None

    orchestrator = Orchestrator(
        app=FakeApp(),
        docker_adapter=FakeDocker(),
        state_manager=EmptyState()
    )

    orchestrator.down()

class FakeSSH:
    def __init__(self):
        self.last_command = None

    def run(self, command):
        self.last_command = command
        return "CONTAINER ID   IMAGE   STATUS"


class FakeApp:
    name = "testapp"
    vps = "dummy"


def test_ps_lists_containers():
    ssh = FakeSSH()

    orchestrator = Orchestrator(
        app=FakeApp(),
        ssh_client=ssh
    )

    output = orchestrator.ps()

    assert ssh.last_command == 'docker ps -a --filter name=testapp --format "{{.Names}}|{{.Image}}|{{.Status}}"'
    assert "CONTAINER ID" in output