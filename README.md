# DockFleet - Self‑Healing Local Container Orchestrator

**Run, monitor, and automatically heal multi‑service Docker stacks using a single YAML file - no cloud, no Kubernetes, no external APIs.**

---

## <u>The Problem</u>

Managing multiple containers on a single machine is difficult:

- Services crash silently

- Manual restarts are required

- Monitoring is scattered

- Kubernetes is too heavy for small setups

---

## <u>Our Solution</u>

DockFleet provides simple orchestration with built-in self-healing:

- Deploy stacks from YAML

- Monitor service health continuously

- Restart unhealthy containers automatically

- View system status in real time

---

## <u>Key Features</u>

- One file to define all services

- One command to run the entire system

- Automatically fixes crashed services

- Real-time monitoring dashboard

- Resource usage tracking

- Centralized logs

- Fully offline and local

---

## Concepts

### What is a Service?

A service represents a containerized application defined in the DockFleet configuration file.  
Each service specifies the Docker image, ports, restart policy, and optional health checks.

### DockFleet vs Docker Compose

DockFleet is similar to Docker Compose but focuses on lightweight local orchestration with built-in health monitoring and automatic recovery.  
It is designed for small deployments and development environments where Kubernetes would be too complex.

---

## Advanced YAML Features

DockFleet supports advanced configuration for more control over services.

### Example

```
```yaml
services:
  redis:
    image: redis:7
    restart: always
```

```
  api:
    image: nginx:latest
    restart: always
```

    ports:
      - "8000:8000"
    
    depends_on:
      - redis
    
    environment:
      - ENV=production
      - DEBUG=false
    
    resources:
      memory: "512m"
      cpu: 0.5

---

### <u>Setup (Python + Virtual Environment)</u>

#### 1. Requirements

- Python 3.10+ installed

- Git installed

- Docker installed and running (for later phases)

Check Python:

```
python --version 
# or 
python3 --version
```

---

#### 2. Clone this repository

```
git clone https://github.com/<your-username>/Dockfleet
cd Dockfleet
```

---

#### 3. Create a virtual environment

Recommended name: `venv`

**Windows (Command Prompt / PowerShell):**

```
python -m venv venv
```

**macOS / Linux:**

```
python3 -m venv venv
```

This creates a `venv/` folder with an isolated Python environment for Dockfleet.

---

#### 4. Activate the virtual environment

**Windows (Command Prompt):**

```
venv\Scripts\activate
```

**Windows (PowerShell):**

```
venv\Scripts\Activate.ps1
```

**macOS / Linux (bash/zsh):**

```
source venv/bin/activate
```

If activation works, your terminal prompt will start with `(venv)`.

**To deactivate later:**

```
deactivate
```

---

#### 5. Install project dependencies

With the virtual environment **activated**, run:

```
pip install -r requirements.txt
```

This will install FastAPI, Typer, SQLModel/SQLAlchemy, and other libraries used by Dockfleet.

#### 6.Install CLI locally

DockFleet CLI (`dockfleet ...` commands) is provided by this repository itself.  
For development, install it in editable mode inside your virtual environment.

from project root, with venv activated

```
pip install -e .
```

---

#### Run Dashboard

Start the real-time web dashboard using Uvicorn:

```
uvicorn dockfleet.dashboard.api:app --reload --port 8080
```

Then open your browser:

```
http://localhost:8080
```

---

## First Run

After setting up the development environment, you can run DockFleet using the example configuration.

## Quick Start

Run DockFleet using the CLI commands.

### 1. Validate configuration

```
dockfleet validate examples/dockfleet.yaml
```

Checks whether the YAML configuration is valid.

---

### 2. Check system environment

```
dockfleet doctor  
```

Verifies Python version and checks whether Docker is installed and reachable.

---

### 3.Seed service health database

```
dockfleet seed examples/dockfleet.yaml
```

Initializes the database and registers services for health monitoring.

---

### 4. Start services

```
dockfleet up examples/dockfleet.yaml
```

Starts all services defined in the configuration file.

---

### 5. Check running services

```
dockfleet ps
```

Displays currently running containers managed by DockFleet.

---

### 6. Stop services

```
dockfleet down examples/dockfleet.yaml
```

Stops and removes all containers defined in the configuration.

---

## Health Monitoring

DockFleet includes a built-in **health monitoring engine** that continuously checks the status of services and stores results in a SQLite database.

Supported health checks:

- **HTTP checks** – verify API endpoints respond correctly

- **TCP checks** – confirm a port is reachable

- **Process checks** – ensure containers are running

You can run the health monitor using the CLI.

Example:

```
python -m dockfleet.cli.main health-dev examples/dockfleet.yaml --once
```

Example output:

```
[2026-03-11 21:49:20] api: healthy  
[2026-03-11 21:49:20] redis: healthy
```

Health results are stored in SQLite and will later be used by DockFleet to trigger automatic service recovery.

### Auto-Restart

DockFleet can automatically restart unhealthy services.

- If a service fails **3 consecutive health checks**, DockFleet attempts a restart.

Restart policies:

- `always` → service restarts whenever failures occur
- `on-failure` → restart only when a failure is detected
- `never` → service will never restart automatically

### Self-Healing Demo

Run DockFleet in automatic recovery mode:

```
dockfleet self-heal examples/dockfleet.yaml
```

If a service fails health checks three times consecutively,
DockFleet automatically restarts the container according to its restart policy.

### Logs

DockFleet allows viewing service logs directly from the CLI.

Examples:

```
dockfleet logs api
dockfleet logs api --follow
```

The dashboard also supports live log streaming using Server-Sent Events (SSE).

### Log Aggregation

DockFleet provides centralized log aggregation across all services.  

### How it works

- Docker logs are captured from running containers  
- Selected log lines are stored in the SQLite database (`LogEvent` table)  
- Logs can be queried via CLI or API  

### Querying Logs

Logs can be filtered using:  

- `service_name` → filter logs for a specific service    
- `q` → search substring in log messages    
- `limit` → restrict number of results    

Example:  

```
GET /logs?service_name=api&q=error&limit=50
```

### Download Logs

Logs can also be downloaded using:  

```
GET /logs/download?service_name=api
```

This returns logs as a plain text file.  

### Notes

- Log aggregation is local-only (no external services)  
- Only recent logs may be stored depending on configuration

---

### <u>Standout Features</u>

Adds two production-grade observability features that set DockFleet apart from basic container managers: **Crash Analytics** and a **Metrics Endpoint**.

### Crash Analytics

DockFleet tracks every service restart and health failure, giving you deep visibility into which services are unstable and why.

**What it gives you:** Instead of just knowing a service is down, you can see its full restart history, identify the most failure-prone services across your stack, and understand the breakdown of failure reasons (healthcheck timeout, crash loop, manual restart, etc.).

### API Endpoints

| Endpoint                                   | Description                                                                  |
| ------------------------------------------ | ---------------------------------------------------------------------------- |
| `GET /analytics/summary`                   | Overall stability snapshot — total restarts, failures, top unstable services |
| `GET /analytics/unstable-services`         | Top N services ranked by restart count                                       |
| `GET /analytics/restart-history/{service}` | Full restart timeline for a specific service                                 |
| `GET /analytics/failure-reasons/{service}` | Grouped failure reason breakdown for a service                               |

### Query Parameters

All analytics endpoints accept:

- `window_hours` — look back window in hours (default: 24, max: 168)
- `limit` — number of results to return (where applicable)

#### Example

```
GET /analytics/summary?window_hours=48&limit=5
```

```json
{
  "window_hours": 48,
  "total_restarts": 12,
  "total_health_failures": 12,
  "most_unstable_services": [
    {
      "service_name": "api",
      "restarts": 9,
      "last_restart_at": "2026-03-19T14:32:00"
    },
    {
      "service_name": "redis",
      "restarts": 3,
      "last_restart_at": "2026-03-18T22:10:00"
    }
  ]
}
```

---

### Metrics Endpoint

DockFleet exposes a `/metrics` endpoint that returns a real-time system health snapshot — useful for dashboards, alerting, or quick status checks.

**What it gives you:** A single API call that tells you how many services are running, how many are unhealthy, the total number of restarts across the entire stack, and how many health failures occurred in the last 24 hours.

#### Endpoint

```
GET /metrics
```

#### Example Response

```json
{
  "total_services": 4,
  "running_services": 3,
  "unhealthy_services": 1,
  "stopped_services": 0,
  "total_restarts": 17,
  "health_failures": 5,
  "collected_at": "2026-03-19T15:00:00"
}
```

#### Response Fields

| Field                | Description                                  |
| -------------------- | -------------------------------------------- |
| `total_services`     | All services registered in DockFleet         |
| `running_services`   | Services currently in healthy state          |
| `unhealthy_services` | Services currently failing health checks     |
| `stopped_services`   | Services that have been stopped              |
| `total_restarts`     | Cumulative restart count across all services |
| `health_failures`    | Restart events recorded in the last 24 hours |
| `collected_at`       | UTC timestamp of when metrics were collected |

---

### <u>Tech Stack</u>

### Backend

- Python 3.10+
- FastAPI (dashboard & backend)
- Typer (CLI framework)
- SQLite (state storage)
- PyYAML + Pydantic (config validation)
- Docker CLI via subprocess

### Frontend

- Alpine.js
- Tailwind CSS
- Server‑Sent Events for real‑time updates

---

## <u>Why DockFleet Matters</u>

| Tool            | Limitation                        |
| --------------- | --------------------------------- |
| Docker Compose  | No intelligent self‑healing       |
| Kubernetes      | Too complex for small deployments |
| Cloud platforms | Require external services         |

DockFleet delivers production‑grade reliability without heavy infrastructure.

---

## <u>Use Cases</u>

- VPS deployments

- Development environments

- Self-hosted applications

- Small production stacks

---

## <u>Team</u>

| Name                                               | Role                  |
| -------------------------------------------------- | --------------------- |
| [Aayush Kumar Jha](https://github.com/AayushJha31) | Orchestration Engine  |
| [Pratyush Jha](https://github.com/pratyushjha06)   | Health & State System |
| [Sunidhi Singh](https://github.com/Sunidhi037)     | CLI & Config System   |
| [Khushi Kumari](https://github.com/Khushi5155)     | Dashboard & Backend   |

---

## <u>License</u>

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.