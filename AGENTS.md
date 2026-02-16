# Agent Instructions (Scope: repository root and all subdirectories)

## Project Snapshot
- Python-first AI assistant framework (`nanobot`) with optional Node.js WhatsApp bridge (`bridge/`).
- Python packaging/build backend: `hatchling` (`pyproject.toml`).
- CLI entrypoint: `nanobot` (`pyproject.toml` -> `nanobot.cli.commands:app`).
- Module entrypoint also works: `python -m nanobot` (`nanobot/__main__.py`).

## Repository Map
- `nanobot/`: core runtime (agent loop, CLI, channels, tools, providers, config, cron, session, security).
- `tests/`: Python tests (unit + integration-style + local e2e scaffolds).
- `bridge/`: TypeScript WhatsApp bridge.
- `scripts/`: helper scripts (including `codex_bridge.py`, Twitter utilities).
- `workspace/`: runtime prompt/memory scaffolding.
- `Dockerfile`, `DEPLOY.md`: container/deploy materials.

## Verified Toolchain
- Python: `>=3.11` (`pyproject.toml`).
- Node.js: `>=20` for bridge work (`bridge/package.json` -> `engines.node`).

## Setup Commands (Current)
1. Create venv: `python -m venv .venv`
2. Install Python deps: `.venv\Scripts\python -m pip install -U pip`
3. Install project + dev extras: `.venv\Scripts\python -m pip install -e .[dev]`
4. Optional bridge deps: `cd bridge && npm install`
5. Initialize config/workspace: `.venv\Scripts\python -m nanobot onboard`

## Runtime Commands (Current CLI)
- Main: `.venv\Scripts\python -m nanobot --help`
- Core commands:
  - `.venv\Scripts\python -m nanobot onboard`
  - `.venv\Scripts\python -m nanobot gateway`
  - `.venv\Scripts\python -m nanobot agent`
  - `.venv\Scripts\python -m nanobot status`
- Channel commands:
  - `.venv\Scripts\python -m nanobot channels status`
  - `.venv\Scripts\python -m nanobot channels login`
- Cron commands:
  - `.venv\Scripts\python -m nanobot cron list`
  - `.venv\Scripts\python -m nanobot cron add`
  - `.venv\Scripts\python -m nanobot cron remove`
  - `.venv\Scripts\python -m nanobot cron enable`
  - `.venv\Scripts\python -m nanobot cron run`
- Bridge:
  - `cd bridge && npm run build`
  - `cd bridge && npm run start`
  - `cd bridge && npm run dev`

## Test and Lint Commands (Current)
- Python tests: `.venv\Scripts\python -m pytest -q`
- Single file: `.venv\Scripts\python -m pytest -q tests/test_agent_loop.py`
- Lint (full rules from `pyproject.toml`): `.venv\Scripts\python -m ruff check .`
- Lint (functional gate): `.venv\Scripts\python -m ruff check . --select F,E9`
- Docker test helper: `tests/test_docker.sh` (Linux shell required)

## CI Reality
- There is currently no committed `.github/workflows/` pipeline in this repo.
- Use local checks as the effective pre-PR gate:
  - `.venv\Scripts\python -m ruff check . --select F,E9`
  - `.venv\Scripts\python -m pytest -q`
  - If `bridge/` changed: `cd bridge && npm run build`

## Current Testing Notes
- `tests/e2e/test_telegram_flow.py` exists and is runnable in local mocked mode.
- `tests/e2e/test_codex_bridge.py` is currently marked `skip` (requires live bridge/auth fixtures).

## Code Style and Conventions
- Follow `pyproject.toml` ruff config:
  - `line-length = 100`
  - lint families `E,F,I,N,W`
  - `E501` ignored
- Keep edits minimal and task-scoped; avoid unrelated refactors.
- Add/adjust tests when behavior changes.
- Preserve backward compatibility for CLI behavior, tool argument names, and config keys.

## Safety and Scope Rules
- Do not modify user-global directories (for example `~/.codex`, `~/.nanobot`) unless explicitly requested.
- Do not commit secrets, auth material, or real sensitive config values.
- Do not add/change dependencies unless explicitly requested.
- Do not add/modify lockfiles unless explicitly requested.
- Do not edit generated/runtime artifacts:
  - `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.nanobot/`, `memory/`, `__pycache__/`
- Do not use destructive git commands (`reset --hard`, `checkout --`, bulk delete) unless explicitly requested.

## Environment Variables Seen In Repo (Names Only)
- `NANOBOT_*`
- `BRAVE_API_KEY`
- `CODEX_BRIDGE_TIMEOUT`
- `CODEX_AUTH_PATH`
- `CODEX_BRIDGE_PORT`
- `BRIDGE_PORT`
- `AUTH_DIR`
- `AUTH_TOKEN`
- `CT0`
- `PANEL_PASSWORD`
- `API_TOKEN`
