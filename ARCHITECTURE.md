# DockFleet Architecture

This document explains how DockFleet is structured internally and how different parts of the system work together.

DockFleet is designed as a lightweight, local container orchestration tool. Instead of using heavy systems like Kubernetes, it focuses on simplicity while still supporting health monitoring, automatic recovery, centralized logs, and basic analytics.

---

## 1. High‑Level Idea

At a high level, DockFleet does a few simple things:

- Reads a YAML configuration file  
- Validates it  
- Starts Docker containers based on it  
- Continuously checks if services are healthy  
- Restarts them if something goes wrong  
- Surfaces status, logs, and analytics in a dashboard and APIs

Everything revolves around this flow.

---

## 2. Overall Structure

You can think of DockFleet as a pipeline:

```text
CLI → Config Loader → Orchestrator → Docker
                    ↓
               Health Engine → DB (SQLite)
                    ↓
            Dashboard + Analytics + Metrics
```

Each part has a clear responsibility, which keeps the system easier to reason about.

---

## 3. Main Components

### CLI Layer (`dockfleet/cli`)

This is the entry point of the system.

All commands like:

- `dockfleet up`  
- `dockfleet down`  
- `dockfleet validate`  
- `dockfleet ps`  
- `dockfleet logs`  
- `dockfleet doctor`  
- `dockfleet seed`

start from here.

The CLI does not contain business logic. It mainly:

- Parses user input  
- Calls the right functions in `core`, `health`, or `dashboard`  
- Prints output in a readable way

---

### Config Layer (`dockfleet/cli/config.py`)

This layer is responsible for handling the YAML file.

It:

- Loads the configuration  
- Validates fields like `services`, `healthcheck`, `resources`, `environment`, `depends_on`, and global flags like `self_healing`  
- Ensures everything is correct before execution starts

If something is wrong, it fails early instead of letting errors happen at runtime.

---

### Orchestrator (`dockfleet/core/orchestrator.py`)

This is the core of DockFleet.

It decides:

- Which containers to start  
- In what order (based on `depends_on`)  
- When to stop or restart services (when called by CLI or health engine)  
- How to translate resource limits and environment variables into Docker flags

It delegates actual Docker calls to helpers that wrap the Docker CLI.

---

### Docker Layer (helpers in `dockfleet/core`)

This layer actually runs Docker commands via `subprocess`.

Instead of using the Docker SDK, DockFleet uses CLI calls like:

```bash
docker run ...
docker stop ...
docker ps ...
docker stats --no-stream ...
```

This keeps things simple and avoids extra dependencies as long as the Docker CLI is available.

---

### Health Engine (`dockfleet/health`)

This part keeps checking whether services are working properly.

It has two main pieces:

- **HealthChecker** – runs checks (HTTP, TCP, process) for each service  
- **Scheduler** – runs these checks at configured intervals

If a service fails multiple times, the system marks it as unhealthy and may trigger a restart depending on:

- Restart policy (`always`, `on-failure`, `never`)  
- Global or per‑service `self_healing` flag

Health results and restart events are stored in SQLite and later used by the dashboard, analytics, and metrics.

---

### Database Layer (`dockfleet/db`, SQLite)

DockFleet uses SQLite to store runtime data such as:

- Service definitions and status  
- Health check results and timestamps  
- Restart counts and restart history  
- Log metadata (service, timestamp, message, severity) used by the log viewer and crash analytics

The `dockfleet/db` package contains:

- Models (SQLModel/SQLAlchemy)  
- Session/engine setup  
- Shared query helpers used by the health engine, dashboard, and analytics

SQLite was chosen because it’s lightweight, embedded, and requires no additional setup.

---

### Dashboard & API (`dockfleet/dashboard`)

The dashboard is a simple web interface powered by FastAPI + Tailwind + Alpine.js.

It provides:

- Service overview: status (running / unhealthy / restarting / stopped), CPU, memory, uptime, restart count  
- Controls: Restart / Stop actions per service  
- Logs: live log streaming via SSE and a central log viewer with filters and download  
- Analytics: “most unstable services”, restart history charts, failure reason breakdown  
- Metrics: `/metrics` endpoint exposing basic counters

All of this is served from the `dockfleet.dashboard` package using JSON APIs and SSE streams.

---

## 4. How Things Flow

### When you run `dockfleet up`

1. CLI receives the command.  
2. Config layer loads and validates `dockfleet.yaml`.  
3. Orchestrator processes the list of services and resolves `depends_on` order.  
4. Orchestrator builds Docker commands (ports, env, resources) and starts containers via Docker helpers.  
5. Initial state is stored in SQLite so the dashboard and health engine know about services.

---

### Health Monitoring Loop

1. Scheduler in `dockfleet.health` triggers checks at intervals.  
2. HealthChecker runs HTTP/TCP/process checks for each service.  
3. Results (healthy/unhealthy, timestamps, consecutive failures) are stored in SQLite.  
4. If failures exceed a threshold and `self_healing` is enabled:  
   - Health engine asks the orchestrator to restart the service.  
   - Restart events are recorded in the database.  
5. Dashboard and analytics read from the database to show current status, restart counts, and trends.

---

### Logs Flow

- Docker logs are read (e.g., `docker logs -f <container>`).  
- Logs can be streamed to the CLI via `dockfleet logs` or to the dashboard via SSE.  
- A log ingestor can store log metadata in SQLite.  
- The central log viewer and crash analytics query log/restart tables to show filtered logs, failure patterns, and “most unstable services”.

---

## 5. YAML → Docker Mapping

Each service in YAML directly maps to a Docker container.

Key mappings:

- `image` → Docker image  
- `ports` → `-p` flags  
- `environment` → `-e KEY=VALUE` (or equivalent flags)  
- `resources.memory` → `--memory`  
- `resources.cpu` → `--cpus`  
- `restart` → restart policy used by health engine/orchestrator  
- `depends_on` → orchestrator start ordering  
- `healthcheck` (type http/tcp/process, endpoint, interval) → health probes used by the health engine  
- `self_healing` (global or per‑service) → whether auto‑restart is enabled

---

## 6. Design Choices

### Why SQLite?

DockFleet targets single‑machine setups.  
SQLite gives:

- Zero setup (single `.db` file)  
- Good enough performance for health/log data  
- Easy integration with SQLModel/SQLAlchemy

Using a heavier DB like PostgreSQL would add complexity without much value in this context.

---

### Why Typer for CLI?

Typer keeps CLI commands:

- Type‑hinted  
- Self‑documenting (good `--help` output)  
- Easy to extend with subcommands

This fits DockFleet’s need for a clean, multi‑command CLI.

---

### Why subprocess instead of Docker SDK?

Using `subprocess` to call the Docker CLI:

- Avoids extra Python dependencies  
- Works anywhere the Docker CLI works (Linux, macOS, Windows/WSL)  
- Keeps behavior close to what users already do manually

---

### Why not Kubernetes?

Kubernetes solves cluster‑level orchestration and scaling, which is overkill for a single machine or dev box.  
DockFleet is intentionally:

- Single‑node  
- Local  
- Easy to set up and tear down

It aims to be “Docker Compose with self‑healing and observability,” not a mini‑Kubernetes.

---

## 7. Self‑Healing Logic

DockFleet follows a simple approach:

- Run health checks at regular intervals per service.  
- Track consecutive failures in SQLite.  
- If failures exceed a configured limit (e.g., 3 in a row) **and** `self_healing` is enabled:  
  - Ask the orchestrator to restart the container.  
  - Increment restart counters and record a restart event.

Restart behavior respects the `restart` policy:

- `always` – always attempt to restart on failure.  
- `on-failure` – restart only when health checks fail.  
- `never` – never auto‑restart (even if checks fail).

---

## 8. Limitations

Current design trade‑offs:

- Works on a **single machine only** (no distributed orchestration).  
- No built‑in auto‑scaling of replicas.  
- Depends on Docker being installed and running locally.  
- Focuses on container lifecycle, health, logs, and basic analytics – not full enterprise observability.

---

## 9. Future Improvements

Some possible extensions:

- Auto‑scaling support (e.g., scale service replicas based on CPU or errors).  
- Multi‑node / remote host support (SSH, Docker contexts, or agents).  
- Custom health check plugins (user‑defined Python checks).  
- Deeper integration with external monitoring tools via Prometheus metrics or webhooks.

---

## 10. Summary

DockFleet is built as a simple but structured system:

- **CLI** for interaction  
- **Config layer** for YAML loading and validation  
- **Orchestrator** for service lifecycle and Docker interaction  
- **Health engine** for monitoring and self‑healing  
- **DB layer** (SQLite) for persistent state, logs, and analytics  
- **Dashboard & APIs** for real‑time visibility, logs, crash analytics, and metrics

The goal is to provide a lightweight alternative for managing containerized services locally without adding Kubernetes‑level complexity.
