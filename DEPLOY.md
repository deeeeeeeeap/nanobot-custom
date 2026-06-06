# nanobot 低配 VPS 部署指南

目标环境：1C1G Linux VPS，常驻 Telegram / cron / Codex / LiteLLM。默认安装不拉取 Slack、飞书、钉钉、QQ、向量搜索等重依赖。

## 1. 安装

```bash
apt update
apt install -y git python3 python3-venv

git clone https://github.com/deeeeeeeeap/nanobot-custom.git /opt/nanobot
cd /opt/nanobot

python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

可选功能按需安装：

```bash
pip install -e '.[slack]'
pip install -e '.[feishu]'
pip install -e '.[dingtalk]'
pip install -e '.[qq]'
pip install -e '.[whatsapp]'
pip install -e '.[vector]'
pip install -e '.[dev]'
```

## 2. 初始化与诊断

```bash
nanobot setup
nanobot doctor
```

`setup` 会默认应用 `vps-1c1g` profile：

- 关闭语义向量搜索与自动索引。
- 限制 `maxToolIterations` 和工具结果内联长度。
- 启用上下文压缩。
- 大工具结果落盘到 workspace 的 `tool-results/`。
- 收紧日志轮转到 50MB x 3。

如果已有配置，wizard 会保留现有值；密钥只显示遮蔽值。

## 3. 最小配置示例

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot/workspace",
      "model": "openai/gpt-5.3-codex",
      "maxToolIterations": 20,
      "toolResultMaxChars": 8000,
      "compactionEnabled": true,
      "compactionTargetRatio": 0.35
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "你的 Telegram Bot Token",
      "allowFrom": ["你的 Telegram 用户 ID"]
    }
  },
  "providers": {
    "codex": {
      "enabled": true,
      "codexHome": "~/.codex",
      "model": "gpt-5.3-codex",
      "timeout": 300
    },
    "minimax": {
      "apiKey": "你的 MiniMax API Key",
      "apiBase": "https://api.minimaxi.com/v1"
    }
  },
  "tools": {
    "web": {
      "search": {
        "apiKey": "你的 Brave Search API Key（可选）"
      }
    },
    "resultStorage": {
      "enabled": true,
      "thresholdChars": 8000,
      "turnBudgetChars": 60000,
      "path": "tool-results",
      "previewChars": 3000,
      "maxFiles": 500,
      "maxBytes": 268435456,
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

如果你的第三方中转站明确支持 OpenAI `/responses`，可在对应 provider 中启用：

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
      "extraHeaders": {},
      "extraBody": {}
    }
  }
}
```

未确认支持 `/responses` 的中转站继续使用默认 `chat_completions`。

## 4. Codex 登录

Codex Provider 读取 `~/.codex/auth.json`：

```bash
mkdir -p ~/.codex
scp ~/.codex/auth.json root@你的VPS:~/.codex/auth.json
nanobot doctor
```

`doctor` 不会联网验证 token；缺失时只给出本地诊断。

## 5. systemd

```bash
cat > /etc/systemd/system/nanobot.service << 'EOF'
[Unit]
Description=nanobot AI Assistant
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

## 6. 排错流程

```bash
nanobot doctor
journalctl -u nanobot -f
systemctl restart nanobot
```

常见情况：

- `Config missing`：运行 `nanobot setup`。
- `Telegram token missing`：重新运行 setup 或编辑 `~/.nanobot/config.json`。
- `Codex auth missing`：复制 `~/.codex/auth.json` 到 VPS。
- `Optional slack/feishu/... missing`：对应渠道未启用可以忽略；启用后安装对应 extra。
- `VPS profile not fully applied`：运行 `nanobot onboard --wizard --profile vps-1c1g`。
