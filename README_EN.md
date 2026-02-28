<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="500">
  <h1>🦾 Carbon-Core</h1>
  <p>An ultra-lightweight personal AI assistant built on nanobot — Less talk, more action</p>
  <p>
    <a href="README.md">📖 中文</a> •
    <a href="#-quick-start">🚀 Deploy</a> •
    <a href="#-features">✨ Features</a> •
    <a href="#-supported-models">📦 Models</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <img src="https://img.shields.io/badge/tests-186_passed-brightgreen" alt="Tests">
    <img src="https://img.shields.io/badge/channels-8-blueviolet" alt="Channels">
  </p>
</div>

> *"I am Carbon-Core, your carbon-based life's extension in the silicon world. Whether it's probing server vulnerabilities or recommending a song, I'm here."* 🦾

## ✨ Features

<table>
<tr>
<td width="50%">

### 🧠 Intelligence
- 🤖 **Hot-swap models** — Claude / Gemini / GPT / Codex / DeepSeek / MiniMax / Kimi
- 🌐 **Antigravity Gateway** — Multi-account rotation for free Claude & Gemini
- 🔌 **Native Codex Provider** — Direct OpenAI Responses API, no Bridge needed
- 🔐 **Auto OAuth Refresh** — Codex tokens auto-renew (HTTP + CLI fallback)
- 🧬 **Chain-of-Thought** — DeepSeek-R1, Claude Thinking visualization
- 🔄 **Interleaved CoT** — Post-tool-execution reflection for better reasoning
- ⚡ **Anti-idle Intervention** — `tool_choice=required` forces tool use
- 💰 **Prompt Caching** — Automatic cache markers for Anthropic/Claude

</td>
<td width="50%">

### 🛡️ Safety & Reliability
- 🛡️ **Anti-hallucination** — Auto-detects fabricated command output
- 🔒 **Command Safety Rails** — Blocks dangerous ops + controlled `$()` passthrough
- 🔗 **URL Verification** — Validates links returned by models
- 📊 **Real-time Status** — 🤔→🔧→✅ progress at a glance
- 🧠 **Dual-layer Memory** — Long-term facts + event logs, persistent across sessions
- 🔎 **Knowledge Search** — BM25 full-text + optional semantic vector search
- 🔀 **Smart Failover** — Auto-routes Codex/non-Codex models to correct provider

</td>
</tr>
<tr>
<td>

### ⏰ Automation
- ⏰ **Reminder mode** — Scheduled static messages
- 🤖 **Agent mode** — Scheduled full tool-chain tasks (weather, system checks)
- 🎯 **One-shot `at`** — Execute once at a specified time, auto-delete
- 🚀 **Sub-agents** — Dispatch long tasks in the background
- 💓 **Heartbeat** — 24/7 online, auto-restart on crash

</td>
<td>

### 📱 Multi-platform
- Telegram ✅ (reply-to) | Feishu ✅ | DingTalk ✅ | Slack ✅
- Email ✅ | QQ ✅ | Discord ✅ (auto-split long messages) | WhatsApp ✅

</td>
</tr>
</table>

## 🏗️ Architecture

```
User Message
  ↓
┌─────────────┐    ┌──────────────────────┐
│  8 Channels  │───→│     AgentLoop        │
└─────────────┘    │  ┌─────────────────┐  │
                   │  │ _pick_provider  │  │
                   │  │  for_model()    │  │
                   │  └───┬────────┬────┘  │
                   │      ↓        ↓       │
                   │ CodexProvider  LiteLLM │ ← Auto-routing
                   │  (Responses   Provider │
                   │   API + SSE)  (General)│
                   │      ↓        ↓       │
                   │  CodexAuth   litellm   │
                   │  (Auto OAuth  (Multi-  │
                   │   refresh)   model)    │
                   └──────────────────────┘
                          ↓
                   ┌──────────────┐
                   │  Tool Chain   │
                   │ Shell / File  │
                   │ Web / Memory  │
                   │ Cron / Search │
                   └──────────────┘
```

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/deeeeeeeeap/nanobot-custom.git /opt/nanobot
cd /opt/nanobot

apt install pipx -y && pipx ensurepath && source ~/.bashrc
pipx install -e . --force
pip install croniter --break-system-packages       # Cron dependency
pip install sentence-transformers --break-system-packages  # Semantic search (optional)
```

### 2. Initialize

```bash
nanobot onboard
```

### 3. Configure `~/.nanobot/config.json`

```json
{
  "agents": {
    "defaults": {
      "model": "openai/gpt-5.3-codex",
      "model_fallbacks": ["antigravity/claude-opus-4-6-thinking"]
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN",
      "allow_from": ["YOUR_USER_ID"]
    }
  },
  "providers": {
    "codex": {
      "enabled": true,
      "codex_home": "~/.codex",
      "model": "gpt-5.3-codex",
      "timeout": 120
    },
    "antigravity": {
      "api_key": "YOUR_API_KEY",
      "api_base": "http://127.0.0.1:8045/v1"
    }
  },
  "tools": {
    "web": {
      "search": {
        "api_key": "YOUR_BRAVE_SEARCH_API_KEY"
      }
    }
  }
}
```

### 4. Codex Authentication

The Codex Provider requires an OAuth token stored at `~/.codex/auth.json`:

```bash
# Option A (recommended): Copy token from a logged-in local machine
scp ~/.codex/auth.json root@YOUR_VPS:~/.codex/auth.json

# Option B: Login via Codex CLI on the VPS
npx @anthropic-ai/codex   # Enter interactive mode and complete authorization
```

> 💡 Tokens auto-refresh when expired (HTTP refresh → CLI fallback). No manual intervention needed.

### 5. Antigravity Gateway (Optional)

```bash
docker run -d --name antigravity-manager \
  -p 8045:8045 \
  -e API_KEY=YOUR_API_KEY \
  -v ~/.antigravity_tools:/root/.antigravity_tools \
  lbjlaq/antigravity-manager:latest
```

### 6. systemd Service

```bash
cat > /etc/systemd/system/nanobot.service << 'EOF'
[Unit]
Description=Carbon-Core AI Assistant
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/nanobot
ExecStart=/root/.local/bin/nanobot gateway
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable --now nanobot
```

## 🔧 Operations

```bash
# View logs
journalctl -u nanobot -f

# Quick update
cd /opt/nanobot && git pull && systemctl restart nanobot

# Smoke test (verify Codex connectivity)
python3 scripts/smoke_test_codex.py --model openai/gpt-5.3-codex
```

## 📱 Telegram Commands

| Command | Description |
|---------|-------------|
| `/model` | View current model & available providers |
| `/model <name>` | Hot-swap model |
| `/status` | System status |
| `/new` | Consolidate memory and start new conversation |
| `/help` | Show available commands |
| `/clear` | Clear session history (disk + memory cache) |

## 🔌 Native Codex Provider

Direct integration with the OpenAI Responses API (`chatgpt.com/backend-api/codex/responses`), no Bridge required:

- **Streaming SSE** — Line-by-line response parsing, no full buffering
- **Auto OAuth Refresh** — `CodexAuth` manages token lifecycle (HTTP refresh → CLI fallback)
- **Atomic Save** — Single-use refresh tokens protected via tmp file + `os.replace`
- **Concurrency Safe** — `asyncio.Lock` prevents parallel token refresh races
- **Server-side Compaction** — Optional server-side context compaction (disabled by default)
- **Smart Routing** — `_pick_provider_for_model()` auto-routes codex/non-codex models to correct provider

## 📦 Supported Models

| Provider | Model | Function Calling | Notes |
|----------|-------|:----------------:|-------|
| **Codex** | `openai/gpt-5.3-codex` | ✅ | Native Provider, auto OAuth refresh |
| Antigravity | `antigravity/claude-opus-4-6-thinking` | ✅ | Gateway rotation |
| Antigravity | `antigravity/gemini-3-flash-preview` | ✅ | Gateway rotation |
| Antigravity | `antigravity/gemini-3-pro` | ✅ | Gateway rotation |
| MiniMax | `minimax/MiniMax-M2.1` | ✅ | |
| Gemini | `gemini-2.5-flash-preview` | ✅ | |
| Claude | `anthropic/claude-sonnet-4-5` | ✅ | |
| DeepSeek | `deepseek/deepseek-chat` | ✅ | |
| Kimi | `moonshot/kimi-k2.5` | ✅ | |

## 🧪 Testing

```bash
# Full test suite
pytest -q

# Codex-specific tests
pytest tests/test_codex_provider.py tests/test_codex_auth.py tests/test_codex_adapter.py -v
```

## 📁 Project Structure

```
nanobot/
├── agent/           # AgentLoop core + tool registry + hallucination detection
├── providers/
│   ├── codex_provider.py   # Native Codex Responses API Provider
│   ├── codex_auth.py       # OAuth token auto-refresh management
│   ├── codex_adapter.py    # Message format conversion + orphan tool cleanup
│   ├── litellm_provider.py # General multi-model adapter via LiteLLM
│   └── base.py             # Provider abstract base class
├── channels/        # 8 messaging channel adapters
├── config/          # Config loading & Pydantic Schema
├── memory/          # Dual-layer memory (compress/extract/dedup)
├── search/          # FTS5 full-text index + vector search
├── session/         # Session management & persistence
├── cron/            # Scheduled task engine
├── cli/             # Typer CLI commands
└── prompts/         # Jinja2 prompt templates
```

## 🙏 Acknowledgments

This project is forked from [HKUDS/nanobot](https://github.com/HKUDS/nanobot) v0.1.4.post1, with extensive custom development on top.

---

<p align="center">
  🦾 Carbon-Core — Less talk, more action
</p>
