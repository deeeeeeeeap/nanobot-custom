<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="500">
  <h1>🦾 碳核 (Carbon-Core)</h1>
  <p>基于 nanobot 的超轻量级个人 AI 助手</p>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</div>

## ✨ 特性

🪶 **超轻量**：核心代码仅 ~3,400 行

🤖 **多模型支持**：Claude、Gemini、GPT、MiniMax、DeepSeek 等

🌐 **Antigravity 网关**：支持 Google Antigravity 多账号轮换，免费使用 Claude 和 Gemini

📱 **Telegram 集成**：随时随地通过 Telegram 与 AI 对话

🔍 **网络搜索**：集成 Brave Search，实时获取网络信息

⚡ **模型热切换**：通过 `/model` 命令随时切换 AI 模型

📊 **实时状态反馈**：工具执行时显示实时进度（🤔→🔧→✅）

🛡️ **防幻觉机制**：自动检测和拦截模型编造的虚假信息

🧠 **思维链支持**：支持 DeepSeek-R1、Claude Thinking 等模型的推理过程输出

## 🚀 快速部署

### 1. 克隆项目

```bash
git clone https://github.com/deeeeeeeeap/nanobot-custom.git /opt/nanobot
cd /opt/nanobot
```

### 2. 安装

```bash
# 安装 pipx（如果没有）
apt install pipx -y
pipx ensurepath
source ~/.bashrc

# 安装 nanobot
pipx install -e . --force
```

### 3. 初始化配置

```bash
nanobot onboard
```

### 4. 配置 API Keys

编辑 `~/.nanobot/config.json`：

```json
{
  "agents": {
    "defaults": {
      "model": "antigravity/claude-opus-4-6-thinking"
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
    "antigravity": {
      "api_key": "你的Antigravity-API-Key",
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

### 5. 部署 Antigravity 网关（可选）

通过 Docker 部署 [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager)，支持多 Google 账号轮换：

```bash
docker run -d --name antigravity-manager \
  -p 8045:8045 \
  -e API_KEY=你的API密钥 \
  -v ~/.antigravity_tools:/root/.antigravity_tools \
  lbjlaq/antigravity-manager:latest
```

然后访问 `http://服务器IP:8045` 添加 Google 账号。

### 6. 创建 systemd 服务

```bash
cat > /etc/systemd/system/nanobot.service << 'EOF'
[Unit]
Description=nanobot AI Assistant
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

systemctl daemon-reload
systemctl enable nanobot
systemctl start nanobot
```

## 📱 Telegram 命令

| 命令 | 说明 |
|------|------|
| `/start` | 开始使用 |
| `/model` | 查看当前模型和可用 providers |
| `/model <模型名>` | 切换模型（会显示能力警告） |
| `/status` | 查看当前状态 |
| `/clear` | 清除会话历史 |

**切换模型示例**：
```
/model antigravity/claude-opus-4-6-thinking
/model antigravity/gemini-3-flash-preview
/model openai/gpt-5.3-codex
```

## 🔧 常用操作

```bash
# 查看状态
nanobot status

# 重启服务
systemctl restart nanobot

# 查看日志
journalctl -u nanobot -f

# 快速更新（保留配置）
cd /opt/nanobot && git pull && pipx install -e . --force && systemctl restart nanobot
```

## 📦 支持的模型

| Provider | 模型 | Function Calling | 说明 |
|----------|------|:----------------:|------|
| Antigravity | `antigravity/claude-opus-4-6-thinking` | ✅ | Claude 思维链模型 |
| Antigravity | `antigravity/gemini-3-flash-preview` | ✅ | Gemini 3 高速模型 |
| Antigravity | `antigravity/gemini-3-pro` | ✅ | Gemini 3 Pro |
| OpenAI | `openai/gpt-5.3-codex` | ❌ | Codex 自主执行 |
| MiniMax | `minimax/MiniMax-M2.1` | ✅ | 性价比高 |
| Gemini | `gemini-2.5-flash-preview` | ✅ | 直连 Google API |
| Claude | `anthropic/claude-sonnet-4-5` | ✅ | 直连 Anthropic |
| DeepSeek | `deepseek/deepseek-chat` | ✅ | 国产模型 |
| Kimi | `moonshot/kimi-k2.5` | ✅ | Moonshot 模型 |

## 🌐 Antigravity 网关

Antigravity 网关通过 [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager) 实现，提供以下功能：

- 🔄 **多账号轮换**：自动在多个 Google 账号间切换
- 🔑 **Token 自动刷新**：无需手动管理 OAuth Token
- 📊 **配额管理**：实时查看每个账号的使用量
- 🛡️ **独立路由**：与 OpenAI/Codex 端点完全隔离

## 🛡️ 防幻觉机制

当模型不支持或未正确调用工具时，系统会自动检测并拦截虚假信息：

- ❌ 不会假装执行命令并编造输出
- ❌ 不会假装搜索并编造结果
- ✅ 如果无法执行，会明确告知用户

## 📊 实时状态反馈

执行工具时，会显示实时进度：

```
🤔 正在思考...
🔧 💻 正在执行命令: df -h
✅ 命令执行完成
[最终回复]
```

## 🙏 致谢

本项目基于 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 修改。

---

<p align="center">
  🦾 碳核 - 你的私人 AI 助手
</p>
