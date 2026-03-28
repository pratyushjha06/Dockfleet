# Contributing to DockFleet

First off, thank you for your interest in contributing to DockFleet!  
This project is built as a clean, FOSS‑friendly local orchestration tool, and we welcome improvements.

This guide explains the development setup, Git workflow, coding standards, and how to propose changes.

---

## 1. Development Setup

For full installation and basic usage, first follow the **Installation** section in the  [User Guide](USER_GUIDE.md).

Once you can run `dockfleet --help`, complete these extra steps for development:

```bash
# From the project root, inside your virtualenv
pip install -e .     # editable install of the DockFleet CLI
pytest               # run test suite to verify everything passes
```

If `pytest` fails on a fresh clone, please open an issue before starting large changes.

---

## 2. Git Workflow

We keep `main` stable. All work happens on short‑lived branches.

### 2.1 Sync with upstream

Before starting any work:

```bash
git checkout main
git pull upstream main   # fetch latest from source repo
git push origin main     # update your fork
```

### 2.2 Create a feature branch

Use clear, kebab‑case branch names:

```bash
git checkout -b feature/orchestrator-core
# or
git checkout -b feature/health-engine
git checkout -b fix/health-check-timeout
git checkout -b docs/update-user-guide
```

### 2.3 Commit style

- Commit small, focused changes every 1–3 hours of work.  
- Use conventional, task‑based prefixes:

```text
feat: add YAML schema validation
fix: handle docker not installed error
docs: add quickstart to user guide
test: add scheduler health engine tests
chore: refactor orchestrator helpers
```

- Keep PRs reasonably small (~300–400 lines) and focused on one logical change.

### 2.4 Opening a Pull Request

1. Push your branch:

```bash
git push origin feature/<short-description>
```

2. Open a PR to `main` on the upstream repository.
3. In the PR description, include:
   - What you changed
   - How to test it (commands / endpoints)
   - Screenshots or GIFs for UI changes (dashboard/logs/analytics)

We prefer at least one review before merging.  
PRs are typically merged via **squash** or **rebase** to keep history clean.

---

## 3. Project Layout (Quick Map)

- `dockfleet/cli/` – Typer CLI commands (`validate`, `doctor`, `seed`, `up`, `down`, `ps`, `logs`, etc.)
- `dockfleet/core/` – Orchestrator and Docker wrapper: service lifecycle, restart logic, resource limits, depends_on.
- `dockfleet/health/` – Health engine: HTTP/TCP/process probes, scheduler, SQLite models, auto‑restart.
- `dockfleet/dashboard/` – FastAPI backend + SSE endpoints and Tailwind/Alpine frontend.
- `tests/` – Unit tests and integration/API tests.

For a deeper explanation of modules and data flow, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 4. Coding Standards

### 4.1 Python style

- Use **black** for formatting and **ruff** (or flake8‑style rules) for linting, if configured.
- Prefer type hints for all new functions and public APIs.
- Keep functions focused and small; avoid large, multi‑purpose functions.

Typical pre‑commit checks:

```bash
black dockfleet tests
ruff dockfleet tests
pytest
```

If you add tools like `pre-commit`, include instructions in this section.

### 4.2 Tests

We care about basic coverage for core behavior. Good places to add tests:

- **CLI & config** – YAML parsing, `validate`, `doctor`, error messages.
- **Orchestrator** – building Docker commands, resource flags, depends_on ordering (Docker calls may be mocked).
- **Health engine** – scheduler timing, failure thresholds (3 failures → restart), restart policies.
- **Dashboard/API** – smoke tests for `/services`, `/logs`, `/analytics`, `/metrics`.

If your change alters behavior, please add or update tests when possible.

Run:

```bash
pytest
```

before opening a PR.

---

## 5. What to Work On

Good first issues:

- Improve error messages and validation for `dockfleet.yaml`.
- Small dashboard polish (responsiveness, dark mode consistency).
- Adding tests around orchestrator or health engine logic.
- Documentation clarifications in `USER_GUIDE.md` or `ARCHITECTURE.md`.

For larger features (new endpoints, major refactors, new analytics views),  
please open an issue first to discuss design and avoid duplicate work.

---

## 6. Reporting Bugs and Requesting Features

**Bugs**

Open a GitHub issue and include:

- DockFleet version and commit hash (if possible)  
- OS and Docker version  
- Exact commands you ran  
- Expected vs actual behavior  
- Any relevant logs or screenshots

**Feature requests**

Explain:

- The problem you want to solve  
- Why it fits DockFleet (local, FOSS, Docker‑only orchestration)  
- Rough idea of implementation if you have one

---

## 7. Code of Conduct

Please be respectful and constructive in all interactions.  
We follow a simple rule: *be kind, be clear, and assume good intent*.

---

Thank you for helping make DockFleet better. 
If you are unsure where to start, feel free to open an issue or draft PR and we’ll help guide you.