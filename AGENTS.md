# Agent Instructions (Scope: repository root and all subdirectories)

## Source Of Truth
- Keep this file aligned with real code/config.
- If this file conflicts with `pyproject.toml`, `bridge/package.json`, `.github/workflows/ci.yml`, or `nanobot/cli/commands.py`, follow those files.

## Project Snapshot
- Python-first assistant framework (`nanobot`) with optional Node.js WhatsApp bridge (`bridge/`).
- Python build backend: `hatchling` (`pyproject.toml`).
- Python CLI entrypoint: `nanobot` -> `nanobot.cli.commands:app`.
- Module entrypoint: `python -m nanobot` (`nanobot/__main__.py`).

## Repository Map
- `nanobot/`: runtime core (agent loop, CLI, channels, tools, providers, config, cron, session, memory, search, security).
- `tests/`: unit/integration/e2e-style tests.
- `bridge/`: TypeScript WhatsApp bridge.
- `scripts/`: helper scripts (`codex_bridge.py`, twitter utilities).
- `workspace/`: runtime prompt/memory scaffolding.
- `.github/workflows/ci.yml`: CI definition.
- `Dockerfile`, `DEPLOY.md`: container/deployment artifacts.

## Verified Toolchain
- Python: `>=3.11` (`pyproject.toml`).
- Node.js: `>=20` for bridge (`bridge/package.json`).

## Setup Commands
1. `python -m venv .venv`
2. `.venv\Scripts\python -m pip install -U pip`
3. `.venv\Scripts\python -m pip install -e .[dev]`
4. Optional bridge install: `cd bridge && npm install`
5. Initialize config/workspace: `.venv\Scripts\python -m nanobot onboard`

## Runtime Commands
- Main help: `.venv\Scripts\python -m nanobot --help`
- Core:
  - `.venv\Scripts\python -m nanobot onboard`
  - `.venv\Scripts\python -m nanobot gateway`
  - `.venv\Scripts\python -m nanobot agent`
  - `.venv\Scripts\python -m nanobot status`
- Channels:
  - `.venv\Scripts\python -m nanobot channels status`
  - `.venv\Scripts\python -m nanobot channels login`
- Cron:
  - `.venv\Scripts\python -m nanobot cron list`
  - `.venv\Scripts\python -m nanobot cron add`
  - `.venv\Scripts\python -m nanobot cron remove`
  - `.venv\Scripts\python -m nanobot cron enable`
  - `.venv\Scripts\python -m nanobot cron run`
- Search:
  - `.venv\Scripts\python -m nanobot search status`
  - `.venv\Scripts\python -m nanobot search reindex`
  - `.venv\Scripts\python -m nanobot search query "<query>"`
  - `.venv\Scripts\python -m nanobot search embed`
- Memory:
  - `.venv\Scripts\python -m nanobot memory status`
  - `.venv\Scripts\python -m nanobot memory list`
  - `.venv\Scripts\python -m nanobot memory show <path>`
  - `.venv\Scripts\python -m nanobot memory compress`
  - `.venv\Scripts\python -m nanobot memory clear`
- Bridge:
  - `cd bridge && npm run build`
  - `cd bridge && npm run start`
  - `cd bridge && npm run dev`

## Test And Lint Commands
- Full tests: `.venv\Scripts\python -m pytest -q`
- Single test file: `.venv\Scripts\python -m pytest -q tests/test_agent_loop.py`
- Ruff full: `.venv\Scripts\python -m ruff check .`
- Ruff CI gate: `.venv\Scripts\python -m ruff check . --select F,E9`
- Docker helper (Linux shell): `tests/test_docker.sh`

## CI Reality
- Workflow: `.github/workflows/ci.yml`
- Python job (always): Python 3.11, `pip install -e .[dev]`, `ruff check . --select F,E9`, `pytest -q`.
- Bridge job (conditional): runs only when `bridge/**` changes; Node 20; `npm install`; `npm run build`.

## Testing Notes
- `tests/e2e/test_telegram_flow.py` is present and runnable in local mocked mode.
- `tests/e2e/test_codex_bridge.py` is marked `skip` (requires live bridge/auth fixtures).

## Code Style And Conventions
- Ruff config (`pyproject.toml`):
  - `line-length = 100`
  - `select = [E, F, I, N, W]`
  - `ignore = [E501]`
- Keep changes task-scoped; avoid unrelated refactors.
- Add or update tests when behavior changes.
- Preserve backward compatibility for CLI behavior, tool argument names, and config keys unless explicitly required.

## Safety And Scope Rules
- Do not modify user-global directories (for example `~/.codex`, `~/.nanobot`) unless explicitly requested.
- Do not commit secrets or real credentials.
- Do not add/change dependencies unless explicitly requested.
- Do not add/modify lockfiles unless explicitly requested.
- Do not edit generated/runtime artifacts:
  - `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.nanobot/`, `memory/`, `__pycache__/`
- Do not use destructive git commands (`reset --hard`, `checkout --`, bulk delete`) unless explicitly requested.

## Environment Variables Seen In Repo (Names Only)
- `NANOBOT_*`
- `BRAVE_API_KEY`
- `CODEX_AUTH_PATH`
- `CODEX_BRIDGE_PORT`
- `CODEX_BRIDGE_TIMEOUT`
- `BRIDGE_PORT`
- `AUTH_DIR`
- `AUTH_TOKEN`
- `CT0`
- `PANEL_PASSWORD`
- `API_TOKEN`
- `NANOBOT_TMUX_SOCKET_DIR`
