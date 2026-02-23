# Agent Instructions (Scope: repository root and all subdirectories)

## 事实来源
- 以仓库实际代码与配置为准。
- 若本文件与以下文件冲突，以这些文件为准：
  - `pyproject.toml`
  - `bridge/package.json`
  - `.github/workflows/ci.yml`
  - `nanobot/cli/commands.py`

## 项目现状
- 主体为 Python 助手框架 `nanobot`，可选 Node.js WhatsApp bridge（`bridge/`）。
- Python 构建后端：`hatchling`。
- Python CLI 入口：`nanobot` -> `nanobot.cli.commands:app`。
- 模块入口：`python -m nanobot`（`nanobot/__main__.py`）。

## 目录映射
- `nanobot/`: 运行时核心（agent loop、CLI、channels、tools、providers、config、cron、session、memory、search、security）。
- `tests/`: 单测与集成/E2E 测试。
- `bridge/`: TypeScript WhatsApp bridge。
- `scripts/`: 辅助脚本（如 `codex_bridge.py`、twitter 相关脚本）。
- `workspace/`: 运行时 prompt/memory 脚手架。
- `.github/workflows/ci.yml`: CI 流水线定义。
- `Dockerfile`、`DEPLOY.md`: 容器与部署相关文件。

## 运行环境
- Python: `>=3.11`（`pyproject.toml`）。
- Node.js: `>=20`（`bridge/package.json`）。

## 初始化命令
1. `python -m venv .venv`
2. `.venv\Scripts\python -m pip install -U pip`
3. `.venv\Scripts\python -m pip install -e .[dev]`
4. 可选 bridge 安装：`cd bridge && npm install`
5. 初始化配置与工作区：`.venv\Scripts\python -m nanobot onboard`

## 当前真实 CLI 命令
- 主命令（`python -m nanobot --help`）：`onboard`、`gateway`、`agent`、`status`、`channels`、`cron`、`search`、`memory`
- Channels：
  - `.venv\Scripts\python -m nanobot channels status`
  - `.venv\Scripts\python -m nanobot channels login`
- Cron：
  - `.venv\Scripts\python -m nanobot cron list`
  - `.venv\Scripts\python -m nanobot cron add`
  - `.venv\Scripts\python -m nanobot cron remove`
  - `.venv\Scripts\python -m nanobot cron enable`
  - `.venv\Scripts\python -m nanobot cron run`
- Search：
  - `.venv\Scripts\python -m nanobot search status`
  - `.venv\Scripts\python -m nanobot search reindex`
  - `.venv\Scripts\python -m nanobot search query "<query>"`
  - `.venv\Scripts\python -m nanobot search embed`
- Memory：
  - `.venv\Scripts\python -m nanobot memory status`
  - `.venv\Scripts\python -m nanobot memory list`
  - `.venv\Scripts\python -m nanobot memory show <path>`
  - `.venv\Scripts\python -m nanobot memory compress`
  - `.venv\Scripts\python -m nanobot memory clear`
- Bridge（Node scripts）：
  - `cd bridge && npm run build`
  - `cd bridge && npm run start`
  - `cd bridge && npm run dev`

## 测试与静态检查
- 全量测试：`.venv\Scripts\python -m pytest -q`
- 单文件测试：`.venv\Scripts\python -m pytest -q tests/test_agent_loop.py`
- Ruff 全量：`.venv\Scripts\python -m ruff check .`
- CI 同款 Ruff gate：`.venv\Scripts\python -m ruff check . --select F,E9`
- Docker 辅助脚本（Linux shell）：`tests/test_docker.sh`

## CI 现状
- Workflow: `.github/workflows/ci.yml`
- `python` job（始终执行）：
  - Python 3.11
  - `pip install -e .[dev]`
  - `ruff check . --select F,E9`
  - `pytest -q`
- `bridge` job（条件执行）：
  - 仅在 `bridge/**` 变更时执行（由 `changes` job + `dorny/paths-filter` 控制）
  - Node 20
  - `npm install`
  - `npm run build`

## 测试备注
- `tests/e2e/test_telegram_flow.py` 存在，可本地 mocked 方式运行。
- `tests/e2e/test_codex_bridge.py` 显式标记为 `skip`（依赖真实 bridge/auth fixtures）。

## 代码约定
- Ruff（`pyproject.toml`）：
  - `line-length = 100`
  - `select = [E, F, I, N, W]`
  - `ignore = [E501]`
- 变更保持任务聚焦，避免无关重构。
- 行为变更需同步补测试或更新现有测试。
- 非明确需求下，保持 CLI 行为、参数名、配置键的向后兼容。

## 安全与范围
- 未明确要求时，不修改用户全局目录（如 `~/.codex`、`~/.nanobot`）。
- 不提交真实密钥或凭证。
- 未明确要求时，不新增/升级依赖，不修改 lockfile。
- 不编辑运行产物或缓存目录（如 `.venv/`、`.pytest_cache/`、`.ruff_cache/`、`__pycache__/`、`.nanobot/`）。
- 未明确要求时，不使用破坏性 git 命令（如 `reset --hard`、`checkout --`、批量删除）。

## 环境变量（仓库内出现的名称）
- `NANOBOT_*`
- `BRAVE_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `DEEPSEEK_API_KEY`
- `GEMINI_API_KEY`
- `ANTHROPIC_BASE_URL`
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
