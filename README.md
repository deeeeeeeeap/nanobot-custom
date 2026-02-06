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

🤖 **多模型支持**：MiniMax M2.1、Gemini、Claude、GPT 等

📱 **Telegram 集成**：随时随地通过 Telegram 与 AI 对话

🔍 **网络搜索**：集成 Brave Search，实时获取网络信息

⚡ **模型热切换**：通过 `/model` 命令随时切换 AI 模型

## 🚀 快速部署

### 1. 克隆项目

```bash
git clone https://github.com/deeeeeeeeap/nanobot-custom.git /root/nanobot
cd /root/nanobot
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
      "model": "minimax/MiniMax-M2.1"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "你的Telegram-Bot-Token",
      "allowFrom": ["你的用户ID"]
    }
  },
  "providers": {
    "minimax": {
      "apiKey": "你的MiniMax-API-Key",
      "apiBase": "https://api.minimaxi.com/anthropic"
    }
  },
  "tools": {
    "web": {
      "search": {
        "apiKey": "你的Brave-Search-API-Key"
      }
    }
  }
}
```

### 5. 创建 systemd 服务

```bash
cat > /etc/systemd/system/nanobot.service << 'EOF'
[Unit]
Description=nanobot AI Assistant
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/nanobot
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
| `/model <模型名>` | 切换模型 |

**切换模型示例**：
```
/model minimax/MiniMax-M2.1
/model gemini-3-flash-preview
```

## 🔧 常用操作

```bash
# 查看状态
nanobot status

# 重启服务
systemctl restart nanobot

# 查看日志
journalctl -u nanobot -f
```

## 📦 支持的模型

| Provider | 模型 | 说明 |
|----------|------|------|
| MiniMax | `minimax/MiniMax-M2.1` | 推荐，性价比高 |
| Gemini | `gemini-3-flash-preview` | Google 免费模型 |
| Claude | `anthropic/claude-sonnet-4-5` | Anthropic 模型 |
| DeepSeek | `deepseek/deepseek-chat` | 国产模型 |

## 🙏 致谢

本项目基于 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 修改。

---

<p align="center">
  🦾 碳核 - 你的私人 AI 助手
</p>
