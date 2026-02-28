<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="500">
  <h1>🦾 碳核 (Carbon-Core)</h1>
  <p>基于 nanobot 的超轻量级个人 AI 助手 — 不空谈，只行动</p>
  <p>
    <a href="README_EN.md">📖 English</a> •
    <a href="#-快速部署">🚀 部署</a> •
    <a href="#-特性一览">✨ 特性</a> •
    <a href="#-支持的模型">📦 模型</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <img src="https://img.shields.io/badge/tests-186_passed-brightgreen" alt="Tests">
    <img src="https://img.shields.io/badge/channels-8-blueviolet" alt="Channels">
  </p>
</div>

> *"我是碳核，你的碳基生命在硅基世界的延伸。无论是探测服务器漏洞，还是想听一首我推荐的歌，我都在。"* 🦾

## ✨ 特性一览

<table>
<tr>
<td width="50%">

### 🧠 智能核心
- 🤖 **多模型热切换** — Claude / Gemini / GPT / Codex / DeepSeek / MiniMax / Kimi
- 🌐 **Antigravity 网关** — 多 Google 账号轮换，免费用 Claude & Gemini
- 🔌 **原生 Codex Provider** — 直连 OpenAI Responses API，无需中间 Bridge
- 🔐 **OAuth 自动刷新** — Codex token 过期自动续期（HTTP + CLI 兜底）
- 🧬 **思维链支持** — DeepSeek-R1、Claude Thinking 推理过程可视化
- 🔄 **交错思维链** — 工具执行后自动反思，提升多步推理质量
- ⚡ **反空转干预** — 执行型请求 `tool_choice=required` 源头阻断空转
- 💰 **Prompt Caching** — Anthropic/Claude 自动注入缓存标记，节省 token

</td>
<td width="50%">

### 🛡️ 安全可靠
- 🛡️ **防幻觉机制** — 自动拦截编造的命令输出、虚假搜索结果
- 🔒 **命令安全护栏** — 智能拦截危险操作 + `$()` 受控放行
- 🔗 **URL 真实性验证** — 模型给出的链接也要查验
- 📊 **实时状态反馈** — 🤔→🔧→✅ 执行进度一目了然
- 🧠 **双层记忆** — 长期事实 + 事件日志，跨会话永久记忆
- 🔎 **知识搜索** — BM25 全文检索 + 可选语义向量搜索
- 🔀 **智能容错** — Codex/非 Codex 模型自动路由 + failover 兜底

</td>
</tr>
<tr>
<td>

### ⏰ 自动化
- ⏰ **提醒模式** — 定时发送静态提醒
- 🤖 **Agent 模式** — 定时执行完整工具链任务（天气预报、系统巡检）
- 🎯 **一次性定时 `at`** — 指定时间执行一次，完成自动删除
- 🚀 **子代理** — 后台派遣长任务，无需持续盯着
- 💓 **心跳保活** — 7×24 在线，崩溃自动重启

</td>
<td>

### 📱 全平台接入
- Telegram ✅ (reply-to 引用) | 飞书 ✅ | 钉钉 ✅ | Slack ✅
- Email ✅ | QQ ✅ | Discord ✅ (长消息自动分片) | WhatsApp ✅

</td>
</tr>
</table>

## 🏗️ 架构概览

```
用户消息
  ↓
┌─────────────┐    ┌──────────────────────┐
│  8 种 Channel │───→│     AgentLoop        │
└─────────────┘    │  ┌─────────────────┐  │
                   │  │ _pick_provider  │  │
                   │  │  for_model()    │  │
                   │  └───┬────────┬────┘  │
                   │      ↓        ↓       │
                   │ CodexProvider  LiteLLM │ ← 自动路由
                   │  (Responses   Provider │
                   │   API + SSE)    (通用) │
                   │      ↓        ↓       │
                   │  CodexAuth   litellm   │
                   │  (OAuth 自动   (多模型  │
                   │   刷新)       适配)    │
                   └──────────────────────┘
                          ↓
                   ┌──────────────┐
                   │  工具链执行    │
                   │ Shell / File  │
                   │ Web / Memory  │
                   │ Cron / Search │
                   └──────────────┘
```

## 🚀 快速部署

### 1. 克隆 & 安装

```bash
git clone https://github.com/deeeeeeeeap/nanobot-custom.git /opt/nanobot
cd /opt/nanobot

# 安装
apt install pipx -y && pipx ensurepath && source ~/.bashrc
pipx install -e . --force
pip install croniter --break-system-packages  # 定时任务依赖
pip install sentence-transformers --break-system-packages  # 语义搜索（可选）
```

### 2. 初始化

```bash
nanobot onboard
```

### 3. 配置 `~/.nanobot/config.json`

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
      "token": "你的Telegram-Bot-Token",
      "allow_from": ["你的用户ID"]
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
      "api_key": "你的API-Key",
      "api_base": "http://127.0.0.1:8045/v1"
    }
  },
  "tools": {
    "web": {
      "search": {
        "api_key": "你的Brave-Search-API-Key"
      }
    }
  }
}
```

### 4. Codex 登录

Codex Provider 需要 OAuth token（存储在 `~/.codex/auth.json`）：

```bash
# 方案 A（推荐）：从已登录的本地机器复制 token
scp ~/.codex/auth.json root@你的VPS:~/.codex/auth.json

# 方案 B：在 VPS 上通过 Codex CLI 登录
npx @anthropic-ai/codex   # 进入交互模式后完成授权
```

> 💡 Token 过期后 Nanobot 会自动刷新（先尝试 HTTP refresh → 失败则 CLI 兜底），无需手动干预。

### 5. 部署 Antigravity 网关（可选）

```bash
docker run -d --name antigravity-manager \
  -p 8045:8045 \
  -e API_KEY=你的API密钥 \
  -v ~/.antigravity_tools:/root/.antigravity_tools \
  lbjlaq/antigravity-manager:latest
```

### 6. systemd 服务

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

## 🔧 日常操作

```bash
# 查看日志
journalctl -u nanobot -f

# 只看 cron 定时任务日志
journalctl -u nanobot --no-pager | grep -i cron

# 快速更新
cd /opt/nanobot && git pull && systemctl restart nanobot

# 冒烟测试（验证 Codex 连通性）
python3 scripts/smoke_test_codex.py --model openai/gpt-5.3-codex
```

## 📱 Telegram 命令

| 命令 | 说明 |
|------|------|
| `/model` | 查看当前模型 & 可用 providers |
| `/model <模型名>` | 热切换模型 |
| `/status` | 系统状态 |
| `/new` | 整合记忆后开始新对话 |
| `/help` | 显示可用命令 |
| `/clear` | 清除会话历史（磁盘 + 内存缓存） |

## 🤖 碳核定制功能

以下功能为本 fork 独有，上游 nanobot 不包含：

### 🔌 原生 Codex Provider

直接调用 OpenAI Responses API（`chatgpt.com/backend-api/codex/responses`），无需 Bridge 中转：

- **流式 SSE 解析** — 逐行处理响应流，不全量缓存
- **OAuth 自动刷新** — `CodexAuth` 管理 token 生命周期（HTTP refresh → CLI 兜底）
- **原子化保存** — 单用 refresh token 通过 tmp 文件 + `os.replace` 防丢
- **并发安全** — `asyncio.Lock` 防止多请求同时刷新 token
- **服务端压缩** — 可选服务端 context compaction（默认关闭）
- **智能路由** — `_pick_provider_for_model()` 自动将 codex/非 codex 模型分发到正确 provider

### 🛡️ 防幻觉系统

```
用户: 帮我查磁盘使用情况
模型: (编造了一段 du -sh 输出)
碳核: ⚠️ 检测到异常 — 我刚才试图用文字描述操作结果...请重新发送请求
```

### ⚡ 反空转干预（v3 — `tool_choice` 源头阻断）

```
执行型请求 → tool_choice="required" → API 层强制模型必须调工具
问答型请求 → tool_choice="auto"      → 正常对话不干预
required 失败 → 自动回退 auto 一次    → 记录 [E_TOOL_CHOICE_FALLBACK] 告警
```

### 🔐 Shell $() 受控放行

```
✅ echo $(date)              — 白名单放行
✅ echo $(cat notes.txt)     — 放行
❌ echo $(rm -rf /)          — 拦截
❌ echo $(date; whoami)      — 复合命令拦截
```

### ⏰ 定时任务 Agent 模式

- **提醒模式**：发送静态文本
- **Agent 模式**：定时触发完整 Agent 处理（可调用所有工具）
- **一次性定时 `at`**：到时执行，完成自动删除
- 120 秒超时 + 错误兜底通知 + session 隔离

### � 双层记忆 + 知识搜索

```
MEMORY.md    — 长期事实，始终加载到上下文
HISTORY.md   — 事件日志，通过 grep 按关键词搜索
index.sqlite — 本地 FTS5 全文索引，BM25 关键词检索
```

```bash
nanobot search status          # 查看索引状态
nanobot search query "关键词"   # CLI 搜索测试
nanobot search reindex          # 手动重建索引
nanobot search embed            # 激活语义搜索
```

### 📋 推特智能监控

- Web 管理面板 — 关注名单管理、凭证配置
- 自动抓取 — 系统 cron 定时抓取关注用户推文
- AI 总结推送 — Agent 读取摘要、智能总结、推送到 Telegram

## 📦 支持的模型

| Provider | 模型 | Function Calling | 备注 |
|----------|------|:----------------:|------|
| **Codex** | `openai/gpt-5.3-codex` | ✅ | 原生 Provider，自动 OAuth 刷新 |
| Antigravity | `antigravity/claude-opus-4-6-thinking` | ✅ | 网关轮换 |
| Antigravity | `antigravity/gemini-3-flash-preview` | ✅ | 网关轮换 |
| Antigravity | `antigravity/gemini-3-pro` | ✅ | 网关轮换 |
| MiniMax | `minimax/MiniMax-M2.1` | ✅ | |
| Gemini | `gemini-2.5-flash-preview` | ✅ | |
| Claude | `anthropic/claude-sonnet-4-5` | ✅ | |
| DeepSeek | `deepseek/deepseek-chat` | ✅ | |
| Kimi | `moonshot/kimi-k2.5` | ✅ | |

## 🧪 测试

```bash
# 运行完整测试套件
pytest -q

# 仅 Codex 相关测试
pytest tests/test_codex_provider.py tests/test_codex_auth.py tests/test_codex_adapter.py -v
```

## 📁 项目结构

```
nanobot/
├── agent/           # AgentLoop 核心 + 工具注册 + 幻觉检测
├── providers/
│   ├── codex_provider.py   # 原生 Codex Responses API Provider
│   ├── codex_auth.py       # OAuth token 自动刷新管理
│   ├── codex_adapter.py    # 消息格式转换 + 孤儿工具清理
│   ├── litellm_provider.py # 通用 LiteLLM 多模型适配
│   └── base.py             # Provider 抽象基类
├── channels/        # 8 种消息渠道适配器
├── config/          # 配置加载 & Pydantic Schema
├── memory/          # 双层记忆（压缩/提取/去重）
├── search/          # FTS5 全文索引 + 向量搜索
├── session/         # 会话管理 & 持久化
├── cron/            # 定时任务引擎
├── cli/             # Typer CLI 命令集
└── prompts/         # Jinja2 提示词模板
```

## 🙏 致谢

本项目 fork 自 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) v0.1.4.post1，在其基础上进行了大量定制开发。

---

<p align="center">
  🦾 碳核 — 不空谈，只行动
</p>
