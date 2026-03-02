import json
import os

class StateManager:
    def __init__(self, app_name):
        os.makedirs(".sidectl", exist_ok=True)
        self.path = f".sidectl/{app_name}_state.json"

    def save(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self):
        with open(self.path) as f:
            return json.load(f)