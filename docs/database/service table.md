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

- status
  - type: TEXT
  - constraints: NOT NULL
  - desc: Current health status (e.g., "running", "unhealthy", "restarting", "stopped").

- restart_count
  - type: INTEGER
  - constraints: NOT NULL, DEFAULT 0
  - desc: Total number of times this service has been restarted by health engine.

- last_health_check
  - type: DATETIME (or TEXT ISO string)
  - constraints: NULL allowed
  - desc: Timestamp of last health check performed for this service.

- last_failure_reason
  - type: TEXT
  - constraints: NULL allowed
  - desc: Short reason of last failure (e.g., "HTTP 500", "TCP timeout", "process not alive").
