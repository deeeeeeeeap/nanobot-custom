---
name: cron
description: Schedule reminders and agent tasks.
---

# Cron 定时任务

使用 `cron` 工具设置定时提醒或定时 Agent 任务。

## 两种模式

### 📨 提醒模式 (`mode="remind"`)
直接发送预设文本，**不经过 Agent 处理**。适合固定提醒。

```
cron(action="add", message="该喝水了！💧", every_seconds=3600, mode="remind")
```

### 🤖 Agent 模式 (`mode="agent"`) ⭐
Agent **在触发时用完整工具链执行指令**：可调用 weather、exec、web_search 等工具，处理结果自动发送给用户。

```
cron(action="add", message="查询景德镇今天的天气预报，温度用摄氏度，格式化后发送给我", cron_expr="0 7 * * *", timezone="Asia/Shanghai", mode="agent")
```

```
cron(action="add", message="检查 /opt 目录磁盘使用情况并报告", every_seconds=86400, mode="agent")
```

## 管理任务

```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## 时间参数

| 用户说 | 参数 |
|--------|------|
| 每 20 分钟 | `every_seconds=1200` |
| 每小时 | `every_seconds=3600` |
| 每天早上 8 点（北京时间） | `cron_expr="0 8 * * *"`, `timezone="Asia/Shanghai"` |
| 工作日下午 5 点 | `cron_expr="0 17 * * 1-5"`, `timezone="Asia/Shanghai"` |

## ⚠️ 注意事项

- 使用 `cron_expr` 时，**务必设置 `timezone`**，否则默认 UTC
- Agent 模式的 `message` 应当是一个完整的指令描述，Agent 会像收到用户消息一样处理
- 提醒模式的 `message` 就是用户会收到的原文
