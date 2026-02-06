# nanobot 新 VPS 部署指南

## 🚀 快速部署

### 1. 上传项目到新 VPS

```bash
# 在新 VPS 上创建目录
mkdir -p /root/nanobot
cd /root/nanobot
```

用 FinalShell 或 scp 将本地 `nanobot-main` 文件夹上传到 `/root/nanobot/`

### 2. 安装 pipx（如果没有）

```bash
apt update
apt install pipx -y
pipx ensurepath
source ~/.bashrc
```

### 3. 安装 nanobot

```bash
cd /root/nanobot
pipx install -e . --force
```

### 4. 初始化配置

```bash
nanobot onboard
```

### 5. 配置 API Keys

```bash
nano ~/.nanobot/config.json
```

填入以下内容（替换占位符）：

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

### 6. 创建 systemd 服务

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
```

### 7. 启动服务

```bash
systemctl daemon-reload
systemctl enable nanobot
systemctl start nanobot
systemctl status nanobot
```

### 8. 查看日志

```bash
journalctl -u nanobot -f
```

---

## 📱 Telegram 命令

- `/start` - 开始使用
- `/model` - 查看/切换模型

## 🔧 常用操作

```bash
# 重启服务
systemctl restart nanobot

# 停止服务
systemctl stop nanobot

# 查看状态
nanobot status

# 查看配置
cat ~/.nanobot/config.json
```
