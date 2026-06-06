<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="460">
  <h1>Carbon-Core / 碳核</h1>
  <p>面向低配 VPS 的个人 AI 助手：Telegram 常驻、定时任务、工具执行、记忆、Codex/LiteLLM 多模型路由。</p>
  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="#低配-vps-推荐">低配 VPS</a> ·
    <a href="#配置">配置</a> ·
    <a href="#部署">部署</a> ·
    <a href="#测试">测试</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/python-%E2%89%A53.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/tests-253%20passed-brightgreen" alt="Tests">
    <img src="https://img.shields.io/badge/default-Telegram%20%2B%20cron%20%2B%20Codex%2FLiteLLM-green" alt="Default install">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</div>

## 项目定位

`nanobot-custom` 是基于 upstream nanobot 深度定制的轻量个人 Agent。当前默认部署目标是：

- 1C1G Linux VPS。
- Telegram 长轮询常驻。
- cron / remind / heartbeat 自动化。
- Codex Provider + LiteLLM fallback。
- 本地 workspace、长期记忆、文件/命令/web/search 工具。
- 默认轻依赖，非核心渠道和向量搜索按需安装。

核心原则：**先稳定运行，再按需加能力**。默认安装不会拉起 Slack、飞书、钉钉、QQ、WhatsApp bridge、Mochat、向量搜索等重组件。

## 特性

### 低配 VPS 优化

- `nanobot setup`：交互式初始化，默认应用 `vps-1c1g` profile。
- `nanobot doctor`：检查 Python、依赖、配置、workspace 写权限、Telegram token、Codex auth、Brave key、低配 profile。
- 默认关闭向量搜索与自动索引，避免低内存机器拉起大模型 embedding 依赖。
- 大工具结果落盘到 workspace 的 `tool-results/`，上下文只保留 preview 和路径。
- `turnBudgetChars` 对单轮工具结果总量生效，避免多个中等结果累计撑爆上下文。
- 日志轮转收紧到 50MB x 3。

### Agent 能力

- 多模型：Codex、OpenAI、Claude、Gemini、DeepSeek、MiniMax、Kimi、OpenRouter、Antigravity 等。
- Codex / 非 Codex 自动路由，支持 fallback。
- 工具链：shell、文件读写、web fetch/search、记忆、cron、子代理、消息发送。
- 自动上下文压缩：超上下文预算时压缩旧消息，保护最近用户请求和最近 tail。
- Prompt caching 友好：稳定 system 前缀、cache fingerprint、cache-safe compaction。
- 防幻觉：拦截编造命令输出、虚假搜索结果等明显异常。
- 反空转：执行型请求优先强制工具调用，减少“只说不做”。

### 渠道与自动化

- 默认渠道：Telegram。
- 可选渠道：Discord、Slack、飞书、钉钉、QQ、WhatsApp、Mochat。
- 定时任务：
  - 静态提醒。
  - Agent 模式定时执行。
  - 一次性 `at` 任务。
  - heartbeat 后台任务。

## 快速开始

### 1. 安装

```bash
git clone https://github.com/deeeeeeeeap/nanobot-custom.git /opt/nanobot
cd /opt/nanobot

python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

默认安装内容：核心运行依赖 + Telegram + cron + Codex/LiteLLM 必需项。

### 2. 初始化

```bash
nanobot setup
nanobot doctor
```

`setup` 会保留已有配置中的密钥；摘要输出会遮蔽 token/API key。

### 3. 启动

```bash
nanobot gateway
```

本地 CLI 测试：

```bash
nanobot agent -m "你好，简单介绍一下当前配置"
```

## 低配 VPS 推荐

1C1G VPS 推荐保持默认轻量安装，只启用 Telegram + 一个主模型 provider：

```bash
pip install -e .
nanobot setup
nanobot doctor
nanobot gateway
```

`vps-1c1g` profile 会写入/调整：

```json
{
  "agents": {
    "defaults": {
      "maxToolIterations": 20,
      "toolResultMaxChars": 8000,
      "compactionEnabled": true,
      "compactionTargetRatio": 0.35
    }
  },
  "memory": {
    "compressThreshold": 30,
    "maxMessageChars": 2000
  },
  "tools": {
    "resultStorage": {
      "enabled": true,
      "thresholdChars": 8000,
      "turnBudgetChars": 60000,
      "path": "tool-results",
      "previewChars": 3000,
      "maxFiles": 100,
      "maxBytes": 67108864,
      "maxAgeDays": 30
    }
  },
  "search": {
    "autoIndex": false,
    "vectorEnabled": false
  },
  "logging": {
    "maxFileBytes": 52428800,
    "maxFiles": 3
  }
}
```

注意：profile 不会关闭你已经启用的 optional channel，但低配机器建议只保留必要渠道。

## 配置

默认配置文件：

```text
~/.nanobot/config.json
```

最小 Telegram + Codex 示例：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot/workspace",
      "model": "openai/gpt-5.3-codex",
      "maxToolIterations": 20,
      "toolResultMaxChars": 8000,
      "compactionEnabled": true
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN",
      "allowFrom": ["YOUR_TELEGRAM_USER_ID"]
    }
  },
  "providers": {
    "codex": {
      "enabled": true,
      "codexHome": "~/.codex",
      "model": "gpt-5.3-codex",
      "timeout": 300
    }
  },
  "tools": {
    "web": {
      "search": {
        "apiKey": ""
      }
    },
    "resultStorage": {
      "enabled": true,
      "thresholdChars": 8000,
      "turnBudgetChars": 60000,
      "path": "tool-results",
      "maxFiles": 100,
      "maxBytes": 67108864,
      "maxAgeDays": 30
    }
  }
}
```

LiteLLM provider 示例：

```json
{
  "agents": {
    "defaults": {
      "model": "minimax/MiniMax-M2.1"
    }
  },
  "providers": {
    "minimax": {
      "apiKey": "YOUR_MINIMAX_API_KEY",
      "apiBase": "https://api.minimaxi.com/v1"
    }
  }
}
```

OpenAI-compatible Responses relay 示例：

```json
{
  "agents": {
    "defaults": {
      "model": "openai/gpt-5-mini"
    }
  },
  "providers": {
    "openai": {
      "apiKey": "YOUR_RELAY_API_KEY",
      "apiBase": "https://relay.example/v1",
      "apiType": "responses",
      "extraHeaders": {
        "X-Relay": "optional"
      },
      "extraBody": {
        "parallel_tool_calls": false
      }
    }
  }
}
```

`apiType` 支持 `chat_completions` 和 `responses`。只有中转站明确支持
`/responses` 时才设为 `responses`；否则保持 `chat_completions`。`extraHeaders`
和 `extraBody` 会原样透传，不会改写 header/body 内部键名。

不要把真实 token/API key 提交到仓库。

## Codex auth

Codex Provider 默认读取：

```text
~/.codex/auth.json
```

推荐从已登录机器复制：

```bash
mkdir -p ~/.codex
scp ~/.codex/auth.json root@YOUR_VPS:~/.codex/auth.json
nanobot doctor
```

`doctor` 只做本地文件存在性检查，不会联网验证 token。

## 可选功能安装

默认不安装非核心渠道 SDK。需要时按需安装：

```bash
pip install -e '.[discord]'
pip install -e '.[slack]'
pip install -e '.[feishu]'
pip install -e '.[dingtalk]'
pip install -e '.[qq]'
pip install -e '.[whatsapp]'
pip install -e '.[mochat]'
pip install -e '.[duckduckgo]'
pip install -e '.[vector]'
pip install -e '.[dev]'
```

说明：

- `.[vector]` 会安装向量搜索依赖，低配 VPS 不建议默认开启。
- `.[duckduckgo]` 只在 `WEB_SEARCH_PROVIDER=duckduckgo` 或配置 DuckDuckGo fallback 时需要。
- `.[dev]` 面向开发/CI；1C1G VPS 不建议安装。
- 未启用的 optional channel 不会 import 对应 SDK。

## 部署

systemd 示例：

```bash
cat > /etc/systemd/system/nanobot.service << 'EOF'
[Unit]
Description=Carbon-Core AI Assistant
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/nanobot
ExecStart=/opt/nanobot/.venv/bin/nanobot gateway
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now nanobot
systemctl status nanobot
```

常用命令：

```bash
journalctl -u nanobot -f
systemctl restart nanobot
nanobot doctor
nanobot status
```

Docker 镜像适合需要 bridge/多渠道的场景；1C1G VPS 最小路径优先使用 venv + systemd，不建议把 Docker 作为默认启动方式。

更多部署细节见 [`DEPLOY.md`](DEPLOY.md)。

## Telegram 命令

| 命令 | 说明 |
| --- | --- |
| `/help` | 查看命令 |
| `/status` | 查看运行状态 |
| `/model` | 查看当前模型和 provider 状态 |
| `/model <模型名>` | 切换模型 |
| `/new` | 整合记忆后开始新会话 |
| `/clear` | 清空当前会话历史 |
| `/stop` | 中断当前会话下的后台子代理 |

## CLI 命令

```bash
nanobot setup
nanobot onboard --wizard --profile vps-1c1g
nanobot doctor
nanobot gateway
nanobot agent -m "hello"
nanobot status
nanobot search status
nanobot search query "关键词"
nanobot memory status
nanobot memory compress --session cli:default
```

## 架构

```text
Telegram / CLI / Cron / Optional Channels
        |
        v
MessageBus + SessionManager
        |
        v
AgentLoop
  - ContextBuilder
  - Compaction / context guard
  - Tool loop detector
  - Hallucination detector
  - Cost/cache tracking
        |
        +--> CodexProvider
        +--> LiteLLMProvider
        |
        +--> ToolRegistry
             - file / shell / web / memory / cron / spawn / message
```

## 目录结构

```text
nanobot/
├── agent/                 # AgentLoop、上下文、工具、缓存指纹、防幻觉
├── channels/              # Telegram 和 optional channels
├── cli/                   # Typer CLI
├── config/                # Pydantic schema 和 loader
├── cron/                  # 定时任务
├── memory/                # 结构化记忆压缩、提取、去重
├── providers/             # Codex / LiteLLM providers
├── search/                # FTS5 和可选向量搜索
└── session/               # 会话持久化
```

## 测试

```bash
python -m pytest -q
```

当前基线：

```text
301 passed, 2 skipped
```

## 排错

优先运行：

```bash
nanobot doctor
```

常见问题：

- `Config missing`：运行 `nanobot setup`。
- `Telegram token missing`：检查 `channels.telegram.token`。
- `Codex auth missing`：检查 `~/.codex/auth.json`。
- optional channel SDK missing：安装对应 extra，或关闭对应 channel。
- VPS profile not fully applied：运行 `nanobot onboard --wizard --profile vps-1c1g`。

## 致谢

本项目 fork 自 [HKUDS/nanobot](https://github.com/HKUDS/nanobot)，并在其基础上增加了 Codex Provider、低配 VPS profile、诊断、轻量安装、上下文/工具结果优化、Telegram/cron 运行增强等定制能力。
