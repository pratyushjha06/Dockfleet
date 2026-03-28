# DockFleet – Self‑Healing Local Container Orchestrator

**Run, monitor, and automatically heal multi‑service Docker stacks using a single YAML file – no cloud, no Kubernetes, no external APIs.**

---

## The Problem

Managing multiple containers on a single machine is difficult:

- Services crash silently  
- Manual restarts are required  
- Monitoring is scattered across tools  
- Kubernetes is too heavy for small setups  

---

## Our Solution

DockFleet provides simple, local orchestration with built‑in self‑healing:

- Deploy multi‑service stacks from YAML  
- Monitor service health continuously (HTTP/TCP/process checks)  
- Restart unhealthy containers automatically based on restart policies  
- View system status in real time through a lightweight dashboard  

---

## Key Features

- One file to define all services (`dockfleet.yaml`)  
- One command to run the entire system (`dockfleet up`)  
- Automatic recovery for crashed/unhealthy containers  
- Real‑time monitoring dashboard (status, health, uptime, restarts)  
- Resource usage tracking (CPU and memory per service)  
- Centralized logs with live streaming and filters  
- Fully offline and local (Docker + SQLite, no external APIs)  

---

## Quick Start (TL;DR)

For full installation and usage details, see the **User Guide** linked below.  
This is the minimal flow once DockFleet is installed locally:

```bash
# 1. Validate configuration
dockfleet validate examples/dockfleet.yaml

# 2. Check environment (Python, Docker, ports)
dockfleet doctor

# 3. Seed service health database
dockfleet seed examples/dockfleet.yaml

# 4. Start all services from YAML
dockfleet up examples/dockfleet.yaml

# 5. See running services
dockfleet ps
```

To launch the real‑time dashboard:

```bash
uvicorn dockfleet.dashboard.api:app --reload --port 8000
# Open http://localhost:8000 in your browser
```

---

## Documentation

- [User Guide](USER_GUIDE.md) – installation, CLI commands, writing `dockfleet.yaml`, and dashboard usage  
- [Architecture & Concepts](ARCHITECTURE.md) – high‑level design, modules, data model, and self‑healing flow  
- [Contributing](CONTRIBUTING.md) – dev setup, branch/PR workflow, code style, and tests  

---

## Status

DockFleet is under active development and is being built for FOSS‑friendly, single‑node environments.  
Feedback and contributions are welcome – see **CONTRIBUTING.md** for how to get involved.