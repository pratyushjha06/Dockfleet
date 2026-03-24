# dockfleet/health/logging.py
import logging
from pathlib import Path

LOG_PATH = Path("dockfleet-health.log")

def setup_health_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
        ],
    )
