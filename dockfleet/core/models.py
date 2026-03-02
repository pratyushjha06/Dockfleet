from dataclasses import dataclass
from typing import List

@dataclass
class Service:
    name: str
    path: str
    port: int

@dataclass
class App:
    name: str
    vps: str
    services: List[Service]