# tests/test_orchestrator.py

from dockfleet.core.orchestrator import Orchestrator

class FakeDockerAdapter:
    def list_containers(self):
        return []

def test_create_plan_when_no_containers_exist():
    desired = {
        "services": {
            "nginx": {
                "image": "nginx:latest"
            }
        }
    }

    state = {}

    orchestrator = Orchestrator(
        docker_adapter=FakeDockerAdapter()
    )

    plan = orchestrator.generate_plan(desired, state)

    assert len(plan.to_create) == 1
    assert plan.to_create[0]["name"] == "nginx"