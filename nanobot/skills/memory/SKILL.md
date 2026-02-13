---
name: memory
description: Two-layer memory system with grep-based recall.
always: true
---

# Memory

## 结构

- `memory/MEMORY.md` — 长期事实（用户偏好、项目上下文、关系等）。始终加载到上下文中。
- `memory/HISTORY.md` — 追加式事件日志。**不会**加载到上下文，需要时通过 grep 搜索。

## 搜索历史事件

```bash
grep -i "keyword" memory/HISTORY.md
```

使用 `exec` 工具执行 grep。可组合模式：`grep -iE "meeting|deadline" memory/HISTORY.md`

## 何时更新 MEMORY.md

遇到重要事实时，立即使用 `edit_file` 或 `write_file` 写入：
- 用户偏好（"我喜欢深色模式"）
- 项目上下文（"API 使用 OAuth2"）
- 人际关系（"Alice 是项目负责人"）

## 自动整合

当会话过长时，旧对话会被自动摘要并追加到 HISTORY.md，长期事实提取到 MEMORY.md。你无需手动管理。
