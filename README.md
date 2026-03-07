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