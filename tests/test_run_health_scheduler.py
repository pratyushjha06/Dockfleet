import logging
import time

from dockfleet.cli.config import load_config, DockFleetConfig
from dockfleet.health.scheduler import HealthScheduler


def test_manual_health_scheduler_run():
    """
    Manual-style test to visually verify HealthScheduler logs
    It will:
    - load examples/dockfleet.yaml
    - start scheduler with 10s interval
    - run for ~30 seconds
    """
    logging.basicConfig(level=logging.INFO)

    # 1) Load config from sample YAML
    config_path = "examples/dockfleet.yaml"
    config: DockFleetConfig = load_config(config_path)

    # 2) Create scheduler (10-second poll interval)
    scheduler = HealthScheduler(config=config, interval_seconds=10)

    # 3) Start scheduler
    scheduler.start()

    # 4) Let it run for a short window so you can see logs
    time.sleep(30)

    # 5) Stop scheduler
    scheduler.stop()


# pytest tests/test_run_health_scheduler.py -s 
# use this for running it
