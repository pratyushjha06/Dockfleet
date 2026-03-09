from datetime import datetime

def get_services_from_db_or_mock():
    """
    Temporary helper for Day 10.
    Currently returns mocked service health data.
    Later this will read from SQLite database.
    """

    services = [
        {
            "name": "api",
            "status": "running",
            "health_status": "healthy",
            "image": "dockfleet_api:latest",
            "ports": "5000:5000",
            "restart_policy": "always",
            "restart_count": 0,
            "last_health_check": datetime.utcnow().isoformat()
        },
        {
            "name": "redis",
            "status": "running",
            "health_status": "healthy",
            "image": "redis:7",
            "ports": "6379:6379",
            "restart_policy": "always",
            "restart_count": 1,
            "last_health_check": datetime.utcnow().isoformat()
        }
    ]

    return services
    