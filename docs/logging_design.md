# Log metadata design 
Goal: central place to index logs for crash analytics and the dashboard, without
storing full raw Docker logs in SQLite.

## LogEvent schema

We introduced a `LogEvent` table:

- `service_id` + `service_name` – which service the log belongs to.
- `created_at` – when this log line/summary was produced.
- `level` – optional level (`INFO`, `WARN`, `ERROR`, etc.).
- `message` – short summary or first line of the log.
- `source` – where this came from (`docker-logs`, `scheduler`, `orchestrator`).

## How logs can be written

Two main writers can populate `LogEvent`:

1. **SSE logs endpoint (Khushi’s part)**  
   - Backend wraps `docker logs -f` per service.  
   - As it streams lines to the dashboard over SSE, it can *optionally* sample
     or aggregate lines and insert `LogEvent` rows (e.g. first error line,
     summary of last N lines, etc.).

2. **Side processes / system components**  
   - Health scheduler and orchestrator can record important events:
     - auto-restart attempts
     - crashes (`status="crashed"`)
     - repeated failures  
   - Instead of writing full logs, they call a helper like
     `record_log_event(service, level="ERROR", message="auto-restart failed")`.

Frontend / dashboard can then:

- Query recent `LogEvent` rows per service for a "Recent events" sidebar.
- Build crash analytics (e.g. “most unstable services”, timeline of errors) from
  this metadata, while still using live SSE for full log streaming.

## Writers: how logs will reach LogEvent

- CLI `dockfleet logs` and the SSE backend that tails `docker logs -f` can
  call `store_log_line(service_name, message, level, source="docker-logs")`
  for sampled / important lines (e.g. only `ERROR` or first line of a burst).

- Health scheduler / orchestrator can also write structured events:
  `store_log_line(name, "auto-restart failed", level="ERROR", source="scheduler")`.

Later, crash analytics / dashboard can query `LogEvent` by `service_id`,
`level`, and `created_at` to show timelines and “most error-prone services”.
