## 1. Responsibilities (Health & State Engine)

- Track per-service status (running / unhealthy / restarting / stopped).
- Auto-restart on failures (based on restart policy).
- Store restart history and basic crash reasons.
- Stay in sync with YAML config (`DockFleetConfig` / `ServiceConfig`).
- Expose a simple API so CLI (`dockfleet seed`) and orchestrator (`up(config)`) can hook in easily.

> Seed time: status is set to "stopped". Unknown / unhealthy states are not used until the health engine is implemented in Week 2.

## 2. Config → Service mapping

Given `DockFleetConfig.services: Dict[str, ServiceConfig]`:

- `name` (dict key) → `Service.name`
- `ServiceConfig.image` → `Service.image`
- `ServiceConfig.restart` → `Service.restart_policy`
  - allowed values: `always`, `on-failure`, `never`
- `ServiceConfig.ports` → `Service.ports_raw` (serialized list, e.g. comma-separated string `"8000:8000,9000:9000"`).
- `ServiceConfig.healthcheck` → `Service.healthcheck_raw` (serialized `HealthCheckConfig` to JSON).

Runtime fields:

- `Service.status`
  - seed time: `"stopped"` (service defined but not yet started).
  - later: `"running"`, `"unhealthy"`, `"restarting"`, `"stopped"` updated by orchestrator + health engine.
- `Service.restart_count`
  - `0` initially.
- `Service.last_health_check`
  - `NULL` initially, set by health engine poll loop.
- `Service.last_failure_reason`
  - `NULL` initially, set when health checks fail.

## 3. Public health-engine functions

### 3.1 `services_from_config(config: DockFleetConfig) -> list[Service]`

- Input: parsed YAML config (`DockFleetConfig`).
- Output: list of `Service` objects (NOT yet written to DB).
- Steps:
  - Loop over `config.services.items()` (`name`, `ServiceConfig`).
  - Map config fields to `Service` fields using the mapping above.
  - Set runtime defaults:

    - `status = "stopped"`  
    - `restart_count = 0`  
    - `last_health_check = None`  
    - `last_failure_reason = None`

  - Return the list of `Service` instances.

### 3.2 `seed_services(config: DockFleetConfig, session: Session) -> None`

- Input: `DockFleetConfig` + SQLModel `Session`.
- Behavior:
  - For each `Service` from `services_from_config(config)`:
    - If a row with the same `Service.name` already exists in DB → leave it as is (do not overwrite status or counters).
    - If it does not exist → insert a new row with `status="stopped"` and other defaults.
- Idempotent:
  - Safe to call multiple times with the same config without creating duplicates.

### 3.3 `bootstrap_from_config(config: DockFleetConfig) -> None`

- Input: in-memory `DockFleetConfig` object.
- Behavior:
  - Calls `init_db()` to ensure SQLite file and tables exist.
  - Opens a `Session(engine)`.
  - Calls `seed_services(config, session)` to sync `Service` rows with YAML.
- Intended usage:
  - Orchestrator can call this at the start of `up(config)` so DB is always seeded before containers start.
  - Tests and scripts can call this directly.

### 3.4 `bootstrap_from_path(path: str = "examples/dockfleet.yaml") -> None`

- Input: path to a DockFleet YAML file.
- Behavior:
  - Calls `load_config(path)` to get a `DockFleetConfig`.
  - Delegates to `bootstrap_from_config(config)`.
- Intended usage:
  - `dockfleet seed` CLI command (Typer) should call this function.
  - `python -m dockfleet.health.seed` currently uses this for local dev.

## 4. Health engine + orchestrator attachment (plan)

- HealthScheduler will periodically read all Service rows from SQLite using a read-only Session.
- For each service, it will decode `healthcheck_raw` (HTTP/TCP/process) and run the appropriate check.
- On success, it will update `Service.status` to `running` and refresh `last_health_check`.
- On failure, it will increment `restart_count`, set `last_failure_reason`, and insert a `RestartEvent` row.
- After N consecutive failures (e.g. 3), it will call an orchestrator restart API (e.g. `restart_service(name)`) instead of talking to Docker directly.
