from sqlmodel import Field, Session, SQLModel, create_engine
from datetime import datetime

# Service table model 
# fields: id, name, images, status, restart_count, last_health_check, last_failure_reason
class Service(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False, unique=True)
    image: str = Field(nullable=False)
    status: str = Field(nullable=False)
    restart_count: int = Field(default=0, nullable=False)
    last_health_check : datetime | None = Field(default=None)
    last_failure_reason: str | None = Field(default=None)


# RestartEvent table model
# fields: id, service_id, restarted_at, reason, previous_status, new_status
class RestartEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    service_id: int = Field(nullable=False, foreign_key="service.id")
    restarted_at: datetime = Field(nullable=False)
    reason: str = Field(nullable=False)
    previous_status: str | None = None
    new_status: str | None = None



# init_db() function 
# work: engine  + tables create 
sqlite_file_name = "dockfleet.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url)

def init_db() -> None:
    SQLModel.metadata.create_all(engine)