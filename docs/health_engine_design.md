## 1. Responsibilities (Health & State Engine)

- Track per-service health status (running / unhealthy / restarting / stopped).
- Auto-restart on failures (based on restart policy).
- Store restart history and basic crash reasons.
- Stay in sync with YAML config (`DockFleetConfig` / `ServiceConfig`).
- Expose a simple API so CLI (`dockfleet seed`) and orchestrator (`up(config)`) can hook in easily.

## 2. Config → Service mapping

Given `DockFleetConfig.services: Dict[str, ServiceConfig]`:

- `name` (dict key) → `Service.name`
- `ServiceConfig.image` → `Service.image`
- `ServiceConfig.restart` → `Service.restart_policy` (values: `always`, `on-failure`, `never`)
- `ServiceConfig.ports` → `Service.ports_raw` (serialized list, e.g. JSON or comma-separated)
- `ServiceConfig.healthcheck` → `Service.healthcheck_raw` (serialized `HealthCheckConfig`)
- Runtime fields set by health engine:
  - `Service.status` → `"unknown"` / `"pending"` initially, later `"running"`, `"unhealthy"`, etc.
  - `Service.restart_count` → `0` initially.
  - `Service.last_health_check` → `NULL` initially.
  - `Service.last_failure_reason` → `NULL` initially.


## 3. Public health-engine functions

### 3.1 from_config(config: DockFleetConfig) -> list[Service]

- Input: parsed YAML config (`DockFleetConfig`).
- Output: list of `Service` objects (NOT yet written to DB).
- Steps:
  - Loop over `config.services.items()` (`name`, `ServiceConfig`).
  - Map config fields to `Service` fields using the mapping above.
  - Set runtime defaults (`status`, `restart_count`, `last_health_check`, `last_failure_reason`).
  - Return the list of `Service` instances.

### 3.2 seed_services(config: DockFleetConfig, session: Session) -> None

- Input: `DockFleetConfig` + SQLModel `Session`.
- Behavior:
  - For each `Service` from `from_config(config)`:
    - If a row with the same `Service.name` already exists in DB → leave as is.
    - If it does not exist → insert a new row.
- Idempotent:
  - Safe to call multiple times with the same config without creating duplicates.

### 3.3 bootstrap_from_config(config: DockFleetConfig) -> None

- Input: in-memory `DockFleetConfig` object.
- Behavior:
  - Calls `init_db()` to ensure SQLite file and tables exist.
  - Opens a `Session(engine)`.
  - Calls `seed_services(config, session)` to sync `Service` rows with YAML.
- Intended usage:
  - Orchestrator can call this before or during `up(config)` if it wants automatic seeding.
  - Tests and scripts can call this directly.

### 3.4 bootstrap_from_path(path: str = "examples/dockfleet.yaml") -> None

- Input: path to a DockFleet YAML file.
- Behavior:
  - Calls `load_config(path)` to get a `DockFleetConfig`.
  - Delegates to `bootstrap_from_config(config)`.
- Intended usage:
  - `dockfleet seed` CLI command (Typer) should call this function.
  - `python -m dockfleet.health.seed` currently uses this for local dev.
