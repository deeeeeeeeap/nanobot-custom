<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="500">
  <h1>🦾 碳核 (Carbon-Core)</h1>
  <p>基于 nanobot 的超轻量级个人 AI 助手 — 不空谈，只行动</p>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <img src="https://img.shields.io/badge/core-~4000_lines-orange" alt="Core">
    <img src="https://img.shields.io/badge/channels-8-blueviolet" alt="Channels">
  </p>
</div>

> *"我是碳核，你的碳基生命在硅基世界的延伸。无论是探测服务器漏洞，还是每天早上7点推送天气预报，我都在。"* 🦾

## ✨ 特性一览

<table>
<tr>
<td width="50%">

### � 智能核心
- �🤖 **多模型热切换** — Claude / Gemini / GPT / DeepSeek / MiniMax / Kimi
- 🌐 **Antigravity 网关** — 多 Google 账号轮换，免费用 Claude & Gemini
- 🧬 **思维链支持** — DeepSeek-R1、Claude Thinking 推理过程可视化
- ⚡ **懒惰检测** — 模型说了"我将要做"但没调工具？自动催促重试

</td>
<td width="50%">

### �️ 安全可靠
- 🛡️ **防幻觉机制** — 自动拦截编造的命令输出、虚假搜索结果
- 🔒 **命令安全护栏** — 智能拦截 `rm -rf`、`format` 等危险操作
- 🔗 **URL 真实性验证** — 模型给出的链接也要查验
- 📊 **实时状态反馈** — 🤔→🔧→✅ 执行进度一目了然

</td>
</tr>
<tr>
<td>

### ⏰ 自动化
- � **提醒模式** — 定时发送静态提醒
- 🤖 **Agent 模式** — 定时执行完整工具链任务（天气预报、系统巡检）
- 🚀 **子代理** — 后台派遣长任务，无需持续盯着
- 💓 **心跳保活** — 7×24 在线，崩溃自动重启

</td>
<td>

### 📱 全平台接入
- Telegram ✅ | 飞书 ✅ | 钉钉 ✅ | Slack ✅
- Email ✅ | QQ ✅ | Discord ✅ | WhatsApp ✅

</td>
</tr>
</table>

## 🚀 快速部署

### 1. 克隆 & 安装

```bash
git clone https://github.com/deeeeeeeeap/nanobot-custom.git /opt/nanobot
cd /opt/nanobot

# 安装
apt install pipx -y && pipx ensurepath && source ~/.bashrc
pipx install -e . --force
pip install croniter --break-system-packages  # 定时任务依赖
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

### 4. 部署 Antigravity 网关（可选）

```bash
docker run -d --name antigravity-manager \
  -p 8045:8045 \
  -e API_KEY=你的API密钥 \
  -v ~/.antigravity_tools:/root/.antigravity_tools \
  lbjlaq/antigravity-manager:latest
```

### 5. systemd 服务

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

## � 日常操作

```bash
# 查看日志
journalctl -u nanobot -f

# 只看 cron 定时任务日志
journalctl -u nanobot --no-pager | grep -i cron

# 快速更新
cd /opt/nanobot && git pull && systemctl restart nanobot
```

## 📱 Telegram 命令

| 命令 | 说明 |
|------|------|
| `/model` | 查看当前模型 & 可用 providers |
| `/model <模型名>` | 热切换模型 |
| `/status` | 系统状态 |
| `/clear` | 清除会话历史（磁盘 + 内存缓存） |

## 🤖 碳核定制功能

以下功能为本 fork 独有，上游 nanobot 不包含：

### 防幻觉系统

```
用户: 帮我查磁盘使用情况
模型: (编造了一段 du -sh 输出)
碳核: ⚠️ 检测到异常 — 我刚才试图用文字描述操作结果...请重新发送请求
```

- 检测模型编造的命令输出、虚假路径列表、伪造搜索结果
- 即使模型支持工具但选择不用，也会触发检测
- URL 真实性验证，拦截虚构链接

### 懒惰检测 & 自动重试

```
模型首轮: "我将使用 exec 工具来执行 curl..."  (has_tool_calls=False)
碳核:     检测到懒惰回复 → 注入催促 → 模型重试 → 真正调用工具 ✅
```

### 定时任务 Agent 模式

```
用户: 每天早上7点给我推送景德镇天气预报
碳核: cron(mode="agent", message="查询景德镇天气...", cron_expr="0 7 * * *", timezone="Asia/Shanghai")
```

- **提醒模式**：发送静态文本
- **Agent 模式**：定时触发完整 Agent 处理（可调用所有工具）
- 120 秒超时 + 错误兜底通知 + session 隔离

### 实时状态反馈

```
🤔 碳核正在思考...
🔧 💻 正在执行命令: df -h
✅ 命令执行完成
[最终回复]
```

### Antigravity 网关兼容

自动解析 Antigravity 的 `{"raw": "..."}` 工具参数格式为标准格式，确保所有工具调用正常工作。

## 📦 支持的模型

| Provider | 模型 | Function Calling |
|----------|------|:----------------:|
| Antigravity | `antigravity/claude-opus-4-6-thinking` | ✅ |
| Antigravity | `antigravity/gemini-3-flash-preview` | ✅ |
| Antigravity | `antigravity/gemini-3-pro` | ✅ |
| OpenAI | `openai/gpt-5.3-codex` | ❌ (自主执行) |
| MiniMax | `minimax/MiniMax-M2.1` | ✅ |
| Gemini | `gemini-2.5-flash-preview` | ✅ |
| Claude | `anthropic/claude-sonnet-4-5` | ✅ |
| DeepSeek | `deepseek/deepseek-chat` | ✅ |
| Kimi | `moonshot/kimi-k2.5` | ✅ |

## 🙏 致谢

本项目 fork 自 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) v0.1.3.post6，在其基础上进行了大量定制开发。

---

<p align="center">
  🦾 碳核 — 不空谈，只行动
</p>
