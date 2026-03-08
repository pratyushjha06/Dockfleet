# Service status → UI mapping

This file documents how we use the `status` field from the `Service` table
and how it should appear in the dashboard UI.

## DB status values

These are the main values we store in `services.status`:

- `running`  
  - Container is up and the latest health check passed.
  - Example: service is started via `dockfleet up` and HTTP/TCP checks are OK.

- `stopped`  
  - Service is defined in config but container is not running.
  - Example: after seeding from YAML, or after `dockfleet down`.

- `unhealthy`  
  - Last health check failed for this service.
  - This is the main candidate state for future auto‑restart logic
    (e.g., "3 failed checks → restart").

- `unknown`  
  - Optional state used before any health check has ever run.
  - In practice we set `stopped` at seed time, so `unknown` is more of a
    placeholder/default than a UI state.

We also track `restart_count`, `last_health_check`, and `consecutive_failures`
in the same table to support health history and auto‑restart behaviour.

## Dashboard UI mapping

The dashboard should map these DB values to simple badges:

- `running`  
  - Label: **Running**  
  - Color: green badge

- `unhealthy`  
  - Label: **Unhealthy**  
  - Color: red badge

- `stopped`  
  - Label: **Stopped**  
  - Color: grey / neutral badge

- `unknown`  
  - Label: **Unknown** or **No data**  
  - Color: yellow / warning badge

