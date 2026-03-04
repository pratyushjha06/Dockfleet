from dockfleet.cli.config import load_config
from dockfleet.health.models import init_db, engine
from sqlmodel import Session 
from dockfleet.health.services import seed_services

def main() -> None:
    config = load_config("examples/dockfleet.yaml")
    init_db()
    with Session(engine) as session:
        seed_services(config, session)

if __name__ == "__main__":
    main()
