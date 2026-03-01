Table: restart_history

- id
  - type: INTEGER
  - constraints: PRIMARY KEY, AUTOINCREMENT
  - desc: Unique restart event ID.

- service_id
  - type: INTEGER
  - constraints: NOT NULL, FOREIGN KEY → services.id
  - desc: Which service was restarted.

- restarted_at
  - type: DATETIME (or TEXT ISO string)
  - constraints: NOT NULL
  - desc: When the restart was triggered.

- reason
  - type: TEXT
  - constraints: NOT NULL
  - desc: Reason for restart (e.g., "3_failed_health_checks", "crash_detected").

- previous_status
  - type: TEXT
  - constraints: NULL allowed
  - desc: Status before restarting (e.g., "unhealthy", "crashed").

- new_status
  - type: TEXT
  - constraints: NULL allowed
  - desc: Status after restart attempt (e.g., "running", "restart_failed").
