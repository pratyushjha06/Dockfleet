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
  - note: Seed time: status is set to "stopped". Unknown / unhealthy states will only be used once the health engine is implemented in Week 2.

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
