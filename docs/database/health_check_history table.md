Table: health_check_history

- id
  - type: INTEGER
  - constraints: PRIMARY KEY, AUTOINCREMENT
  - desc: Unique ID for each health check record.

- service_id
  - type: INTEGER
  - constraints: NOT NULL, FOREIGN KEY → services.id
  - desc: Which service this health check belongs to.

- checked_at
  - type: DATETIME (or TEXT ISO string)
  - constraints: NOT NULL
  - desc: When this health check was performed.

- result
  - type: TEXT
  - constraints: NOT NULL
  - desc: "healthy" or "unhealthy".

- check_type
  - type: TEXT
  - constraints: NOT NULL
  - desc: Type of health check: "http", "tcp", or "process".

- detail
  - type: TEXT
  - constraints: NULL allowed
  - desc: Extra info like HTTP status code, error message, etc.
