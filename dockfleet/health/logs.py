from datetime import datetime
from sqlmodel import Session, select
from .models import LogEvent, Service, engine


def store_log_line(service_name: str, message: str, level: str | None = None, source: str | None = None) -> None:
    """
    Lightweight helper to store a single log metadata row.

    This will be called from CLI/SSE log streaming in Week 3.
    """
    with Session(engine) as session:
        svc = session.exec(
            select(Service).where(Service.name == service_name)
        ).one_or_none()

        if svc is None:
            # Best-effort: we can still store the name with a fake id.
            # For now, just skip to keep it simple.
            print(f"[logs] Service '{service_name}' not found in DB")
            return

        event = LogEvent(
            service_id=svc.id,
            service_name=svc.name,
            created_at=datetime.utcnow(),
            level=level,
            message=message,
            source=source,
        )

        session.add(event)
        session.commit()
