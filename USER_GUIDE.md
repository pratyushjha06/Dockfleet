# DockFleet User Manual

## 1. Requirements

Make sure the following are installed:

- Python 3.10+ installed

- Docker (running)

- Git installed

Check versions:

```
python --version
docker --version
```

---

## 2. Setup

### Clone repository

```
git clone https://github.com/<your-username>/Dockfleet
cd Dockfleet
```

---

### 3. Create a virtual environment

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

### Activate:

**Windows(Command Prompt):**

```
venv\Scripts\activate
```

**Windows (PowerShell):**

```
venv\Scripts\Activate.ps1
```

**macOS/Linux(bash/zsh):**

```
source venv/bin/activate
```

If activation works, your terminal prompt will start with `(venv)`.

**To deactivate later:**

```
deactivate
```

---

### Install  Project dependencies

With the virtual environment **activated**, run:

```
pip install -r requirements.txt
```

This will install FastAPI, Typer, SQLModel/SQLAlchemy, and other libraries used by Dockfleet.

#### Install CLI locally:

DockFleet CLI (`dockfleet ...` commands) is provided by this repository itself.  
For development, install it in editable mode inside your virtual environment.

from project root, with venv activated

```
pip install -e .
```

---

### **Dashboard**

Start dashboard:

```
uvicorn dockfleet.dashboard.api:app --reload --port 8000
```

Then Open your browser:

---

### First Run:

After setting up the development environment, you can run DockFleet using the example configuration.

## Quick Start:

#### <u>CLI Commands:</u>

##### Validate configuration

```
dockfleet validate examples/dockfleet.yaml
```

Checks whether the YAML configuration is valid.

---

##### Check system environment:

```
dockfleet doctor
```

Verifies Python version and checks whether Docker is installed and reachable.

---

##### Seed service health database (Initialize database)

```
dockfleet seed examples/dockfleet.yaml
```

Initializes the database and registers services for health monitoring.

---

##### Start services

```
dockfleet up examples/dockfleet.yaml
```

Starts all services defined in the configuration file.

---

##### Check running services

```
dockfleet ps
```

Displays currently running containers managed by DockFleet.

---

##### Stop services

```
dockfleet down examples/dockfleet.yaml
```

Stops and removes all containers defined in the configuration.

---

### View logs

DockFleet allows viewing service logs directly from the CLI.

```
dockfleet logs api
dockfleet logs api --follow
```

The dashboard also supports live log streaming using Server-Sent Events (SSE)

### Querying Logs

Logs can be filtered using:

- `service_name` → filter logs for a specific service
- `q` → search substring in log messages
- `limit` → restrict number of results

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

---

## 5. Health Monitoring

DockFleet includes a built-in **health monitoring engine** that continuously checks the status of services and stores results in a SQLite database.

You can run the health monitor using the CLI:

```
python -m dockfleet.cli.main health-dev examples/dockfleet.yaml --once
```

Example output:

```
[timestamp] api: healthy
[timestamp] redis: unhealthy
```

Health results are stored in SQLite and will later be used by DockFleet to trigger automatic service recovery.

---

## 7. Common Errors

### Invalid YAML

```
❌ [api] invalid memory '500mb'
```

Fix:

- Use `"512m"` or `"1g"`

---

### Missing dependency

```
❌ [api] depends_on references 'db' which is not defined
```

Fix:

- Ensure service exists in YAML

---

### Docker not running

```
❌ Docker not found
```

Fix:

- Start Docker Desktop / daemon

---

## 8. Quick Workflow

```
dockfleet validate examples/dockfleet.yaml
dockfleet doctor
dockfleet seed examples/dockfleet.yaml
dockfleet up examples/dockfleet.yaml
dockfleet ps
dockfleet down examples/dockfleet.yaml
```

---

This is the minimal flow required to run DockFleet locally.
