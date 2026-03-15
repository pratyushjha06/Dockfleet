Table: services

- id  
  - type: INTEGER  
  - constraints: PRIMARY KEY, AUTOINCREMENT  
  - desc: Internal unique ID for each service.

- name  
  - type: TEXT  
  - constraints: UNIQUE, NOT NULL  
  - desc: Service name from dockfleet.yaml (e.g., "api", "redis").

- image  
  - type: TEXT  
  - constraints: NOT NULL  
  - desc: Docker image for this service (e.g., "my-api:latest", "redis:7").

- restart_policy  
  - type: TEXT  
  - constraints: NOT NULL  
  - desc: Restart policy from config: "always", "on-failure", or "never".

- ports_raw  
  - type: TEXT  
  - constraints: NULL allowed  
  - desc: Serialized ports list from config (e.g., "8000:8000" or "8000:8000,9000:9000").

- healthcheck_raw  
  - type: TEXT  
  - constraints: NULL allowed  
  - desc: Serialized healthcheck config (type/endpoint/interval) for this service.

- status  
  - type: TEXT  
  - constraints: NOT NULL  
  - desc: Current service status (e.g., "running", "unhealthy", "restarting", "stopped").  
  - note: Seed time: status is set to "stopped". Unknown / unhealthy states are used once the health engine runs.

- restart_count  
  - type: INTEGER  
  - constraints: NOT NULL, DEFAULT 0  
  - desc: Total number of times this service has been restarted  
    (manual dashboard restart + auto self‑healing restarts).  
    Health check failures alone do not change this.

- last_health_check  
  - type: DATETIME (ISO string)  
  - constraints: NULL allowed  
  - desc: Timestamp of the last health check performed for this service.

- last_failure_reason  
  - type: TEXT  
  - constraints: NULL allowed  
  - desc: Short reason of last failure (e.g., "HTTP 500", "TCP timeout", "process not alive").

- consecutive_failures  
  - type: INTEGER  
  - constraints: NOT NULL, DEFAULT 0  
  - desc: Number of back‑to‑back failed health checks.  
  - note: Used for "3 failed checks → restart" logic in the health engine.

- resources_memory  
  - type: TEXT  
  - constraints: NULL allowed  
  - desc: Memory limit from config.resources.memory (e.g., "512m").  
  - note: Stored for future dashboard display and crash analytics.

- resources_cpu  
  - type: REAL  
  - constraints: NULL allowed  
  - desc: CPU limit from config.resources.cpu (e.g., 0.5).  
  - note: Stored for future dashboard display and crash analytics.

- env_raw  
  - type: TEXT  
  - constraints: NULL allowed  
  - desc: Serialized environment variables from config.environment  
    (JSON array of strings, e.g., ["DB_URL=postgres://...", "REDIS_URL=..."]).  

- depends_on_raw  
  - type: TEXT  
  - constraints: NULL allowed  
  - desc: Serialized dependency list from config.depends_on  
    (comma‑separated service names, e.g., "redis,db").
