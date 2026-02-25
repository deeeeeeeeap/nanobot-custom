#!/usr/bin/env python3
"""
Codex Bridge v2 — Chat Completions API ↔ ChatGPT Responses API

将 nanobot (LiteLLM) 发出的 Chat Completions 请求转换为
ChatGPT Responses API 格式，发送到 chatgpt.com 后端，
解析 SSE 流式响应后转回 Chat Completions 格式。

支持：
  - 完整对话上下文（所有 messages 均转换）
  - Function Calling（tools / tool_calls 双向转换）
  - SSE 流式解析
  - ChatGPT Plus access_token 认证
  - 可配置超时
"""

import json
import os
import time
import uuid
import traceback

import requests as http_requests
from flask import Flask, request, jsonify

# ============================================================
# 配置
# ============================================================

TIMEOUT_SECONDS = int(os.environ.get("CODEX_BRIDGE_TIMEOUT", 300))
AUTH_PATH = os.environ.get(
    "CODEX_AUTH_PATH",
    os.path.expanduser("~/.codex/auth.json"),
)
LISTEN_PORT = int(os.environ.get("CODEX_BRIDGE_PORT", 8081))
RESPONSES_API_URL = "https://chatgpt.com/backend-api/codex/responses"

# 碳核身份提示词
CARBON_CORE_IDENTITY = """你是「碳核」(Carbon-Core)，一个由 nanobot 驱动的 AI 助手。
重要：你的身份是「碳核」，不是 Codex、ChatGPT 或其他模型的默认身份。
请始终以碳核的身份回应，并在需要时主动使用工具进行操作。"""

app = Flask(__name__)


# ============================================================
# 认证管理
# ============================================================

class AuthManager:
    """读取 Codex CLI 的 auth.json，支持嵌套 tokens 结构。"""

    def __init__(self, auth_path: str):
        self.auth_path = auth_path
        self.access_token: str | None = None
        self.account_id: str | None = None
        self.api_key: str | None = None
        self._load()

    def _load(self):
        """从 auth.json 加载认证信息。"""
        with open(self.auth_path, "r") as f:
            data = json.load(f)

        # 支持 Codex CLI 嵌套格式：{"tokens": {"access_token": ..., "account_id": ...}}
        tokens = data.get("tokens", {})
        self.access_token = tokens.get("access_token") or data.get("access_token")
        self.account_id = tokens.get("account_id") or data.get("account_id")
        self.api_key = data.get("OPENAI_API_KEY") or data.get("api_key")

        if self.access_token:
            print(f"✅ 认证加载成功：ChatGPT Plus (account: {self.account_id[:8]}...)")
        elif self.api_key:
            print(f"✅ 认证加载成功：OpenAI API Key ({self.api_key[:8]}...)")
        else:
            print("⚠️ 警告：未找到有效认证信息")

    def reload(self):
        """重新加载认证文件（token 刷新后调用）。"""
        self._load()

    def get_headers(self) -> dict[str, str]:
        """构造请求头，包含认证和 Cloudflare 绕过所需的浏览器指纹。"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            # 浏览器指纹头（绕过 Cloudflare）
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://chatgpt.com/",
            "Origin": "https://chatgpt.com",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            # Codex 专用头
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            # 随机 session ID
            "session_id": str(uuid.uuid4()),
        }

        # 认证
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
            if self.account_id:
                headers["chatgpt-account-id"] = self.account_id
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        return headers


# 全局认证管理器
auth = AuthManager(AUTH_PATH)


# ============================================================
# 格式转换：Chat Completions → Responses API
# ============================================================

def extract_text(content) -> str:
    """从 Chat Completions 的 content 字段提取纯文本。
    content 可能是 str、list[{type, text}]、或 None。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts)
    return str(content)


def convert_to_responses_api(data: dict) -> dict:
    """将 Chat Completions 请求转换为 Responses API 请求。

    转换规则：
      - system message → instructions 字段
      - user/assistant messages → input items（type: message）
      - assistant.tool_calls → input items（type: function_call）
      - tool message → input items（type: function_call_output）
    """
    messages = data.get("messages", [])

    instructions = CARBON_CORE_IDENTITY
    input_items = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        # system → 提取为 instructions
        if role == "system":
            sys_text = extract_text(content)
            if sys_text:
                instructions = sys_text + "\n\n" + CARBON_CORE_IDENTITY
            continue

        # tool → function_call_output（工具执行结果）
        if role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": extract_text(content),
            })
            continue

        # assistant → 可能包含纯文本和/或 tool_calls
        if role == "assistant":
            tool_calls = msg.get("tool_calls")

            # 先添加 tool_calls（function_call items）
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    input_items.append({
                        "type": "function_call",
                        "call_id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "{}"),
                    })

            # 再添加文本内容（如果有）
            text = extract_text(content)
            if text:
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            continue

        # user（或其他角色）→ message
        input_items.append({
            "type": "message",
            "role": role,
            "content": [{"type": "input_text", "text": extract_text(content)}],
        })

    # 构造 Responses API 请求体
    result = {
        "model": data.get("model", "gpt-5.3-codex"),
        "instructions": instructions,
        "input": input_items,
        "stream": True,
        "store": False,
    }

    # tools 格式转换：Chat Completions 嵌套格式 → Responses API 扁平格式
    # Chat Completions: {"type": "function", "function": {"name": ..., "parameters": ...}}
    # Responses API:    {"type": "function", "name": ..., "parameters": ...}
    tools = data.get("tools")
    if tools:
        converted_tools = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                converted_tool = {"type": "function"}
                converted_tool["name"] = func.get("name", "")
                if "description" in func:
                    converted_tool["description"] = func["description"]
                if "parameters" in func:
                    converted_tool["parameters"] = func["parameters"]
                converted_tools.append(converted_tool)
            else:
                # 非 function 类型或已经是扁平格式，直接透传
                converted_tools.append(tool)
        result["tools"] = converted_tools
        result["tool_choice"] = data.get("tool_choice", "auto")

    return result


# ============================================================
# SSE 响应解析：Responses API → 文本 + tool_calls
# ============================================================

def parse_sse_response(response) -> tuple[str, list[dict]]:
    """解析 Responses API 的 SSE 流式响应。

    返回:
      (text_content, tool_calls)
      - text_content: 模型生成的文本回复
      - tool_calls: Chat Completions 格式的 tool_calls 列表
    """
    text_content = ""
    tool_calls = []
    # 追踪正在流式传输的 function_call 参数
    pending_calls: dict[str, dict] = {}  # item_id → {call_id, name, arguments}

    for raw_line in response.iter_lines():
        # 兼容 bytes 和 str（压缩响应可能返回 bytes）
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = raw_line

        if not line or not line.startswith("data: "):
            continue

        json_str = line[6:]  # 去掉 "data: " 前缀
        if json_str == "[DONE]":
            break

        try:
            event = json.loads(json_str)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        # ---- 文本增量 ----
        if event_type == "response.output_text.delta":
            delta = event.get("delta", "")
            text_content += delta

        # ---- 新的输出项（可能是 function_call）----
        elif event_type == "response.output_item.added":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                item_id = item.get("id", "")
                pending_calls[item_id] = {
                    "call_id": item.get("call_id", f"call_{uuid.uuid4().hex[:8]}"),
                    "name": item.get("name", ""),
                    "arguments": "",
                }

        # ---- function_call 参数增量 ----
        elif event_type == "response.function_call_arguments.delta":
            item_id = event.get("item_id", "")
            delta = event.get("delta", "")
            if item_id in pending_calls:
                pending_calls[item_id]["arguments"] += delta

        # ---- 输出项完成 ----
        elif event_type == "response.output_item.done":
            item = event.get("item", {})
            item_type = item.get("type", "")

            if item_type == "function_call":
                # 优先使用完成事件中的完整数据
                call_id = item.get("call_id", f"call_{uuid.uuid4().hex[:8]}")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                })

            elif item_type == "message":
                # 提取最终文本（覆盖增量累积的内容）
                for ci in item.get("content", []):
                    if ci.get("type") == "output_text" and ci.get("text"):
                        text_content = ci["text"]

        # ---- 响应完成 ----
        elif event_type in ("response.completed", "response.done"):
            # 从完成事件中提取最终输出（兜底）
            resp_output = event.get("response", {}).get("output", [])
            for output_item in resp_output:
                if output_item.get("type") == "message":
                    for ci in output_item.get("content", []):
                        if ci.get("type") == "output_text" and ci.get("text"):
                            text_content = ci["text"]
                elif output_item.get("type") == "function_call":
                    # 检查是否已经在 tool_calls 中
                    call_id = output_item.get("call_id", "")
                    if not any(tc["id"] == call_id for tc in tool_calls):
                        tool_calls.append({
                            "id": call_id or f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": output_item.get("name", ""),
                                "arguments": output_item.get("arguments", "{}"),
                            },
                        })

    return text_content, tool_calls


# ============================================================
# 构造 Chat Completions 响应
# ============================================================

def build_chat_response(
    model: str,
    text_content: str,
    tool_calls: list[dict],
) -> dict:
    """将解析结果组装为 Chat Completions 响应格式。"""
    message: dict = {"role": "assistant"}

    if tool_calls:
        message["tool_calls"] = tool_calls
        # 有 tool_calls 时 content 可以为 null 或包含文本
        message["content"] = text_content if text_content else None
        finish_reason = "tool_calls"
    else:
        message["content"] = text_content or "（无响应内容）"
        finish_reason = "stop"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


# ============================================================
# 核心处理
# ============================================================

def handle_chat(data: dict):
    """处理一个 Chat Completions 请求。"""
    model = data.get("model", "gpt-5.3-codex")
    messages = data.get("messages", [])
    has_tools = bool(data.get("tools"))

    # 日志
    msg_count = len(messages)
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = extract_text(m.get("content", ""))[:100]
            break
    print(f"\n📨 请求: model={model}, messages={msg_count}, tools={has_tools}")
    print(f"   最后用户消息: {last_user}...")

    # 转换格式
    responses_req = convert_to_responses_api(data)

    try:
        # 发送到 ChatGPT Responses API
        resp = http_requests.post(
            RESPONSES_API_URL,
            headers=auth.get_headers(),
            json=responses_req,
            stream=True,
            timeout=TIMEOUT_SECONDS,
        )

        # 检查 HTTP 状态
        if resp.status_code == 401:
            # Token 过期，尝试重新加载
            print("⚠️ Token 过期 (401)，尝试重新加载 auth.json...")
            auth.reload()
            # 用新 token 重试一次
            resp = http_requests.post(
                RESPONSES_API_URL,
                headers=auth.get_headers(),
                json=responses_req,
                stream=True,
                timeout=TIMEOUT_SECONDS,
            )

        if not resp.ok:
            error_body = resp.text[:500]
            print(f"❌ API 错误: {resp.status_code} - {error_body}")
            return jsonify(build_chat_response(
                model,
                f"⚠️ ChatGPT API 错误 ({resp.status_code}): {error_body}\n"
                f"如 Token 过期，请在 VPS 上运行 `codex auth` 刷新。",
                [],
            ))

        # 解析 SSE 响应
        text_content, tool_calls = parse_sse_response(resp)

        if tool_calls:
            print(f"✅ 响应: {len(tool_calls)} 个工具调用")
            for tc in tool_calls:
                func = tc.get("function", {})
                print(f"   🔧 {func.get('name', '?')}({func.get('arguments', '')[:80]})")
        else:
            print(f"✅ 响应: {len(text_content)} 字符文本")

        return jsonify(build_chat_response(model, text_content, tool_calls))

    except http_requests.Timeout:
        print(f"❌ 超时: {TIMEOUT_SECONDS}s")
        return jsonify(build_chat_response(
            model,
            f"⚠️ 请求超时（>{TIMEOUT_SECONDS}秒），请简化问题后重试。",
            [],
        )), 504

    except Exception as e:
        print(f"❌ 异常: {traceback.format_exc()}")
        return jsonify(build_chat_response(
            model,
            f"⚠️ 桥接错误: {str(e)}",
            [],
        )), 500


# ============================================================
# Flask 路由
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    """健康检查。"""
    return jsonify({
        "status": "ok",
        "service": "codex-bridge",
        "version": "2.0",
        "auth": "chatgpt_plus" if auth.access_token else (
            "api_key" if auth.api_key else "none"
        ),
        "timeout": TIMEOUT_SECONDS,
    })


@app.route("/v1/chat/completions", methods=["POST"])
def chat_v1():
    """OpenAI 兼容端点 /v1/chat/completions。"""
    return handle_chat(request.json)


@app.route("/chat/completions", methods=["POST"])
def chat():
    """备用端点 /chat/completions。"""
    return handle_chat(request.json)


@app.route("/responses", methods=["POST"])
@app.route("/v1/responses", methods=["POST"])
def responses_api():
    """兼容 litellm Responses API 格式。

    新版 litellm 对含 'codex' 的模型名强制使用 Responses API，
    发送到 /responses 而非 /chat/completions。
    此路由将 Responses API 格式转换为 Chat Completions 格式后复用现有逻辑。
    """
    data = request.json or {}

    # 将 Responses API 的 input 字段转为 Chat Completions 的 messages 格式
    messages = []
    instructions = data.get("instructions", "")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    for item in data.get("input", []):
        item_type = item.get("type", "")
        if item_type == "message":
            role = item.get("role", "user")
            # 提取文本内容
            content_parts = item.get("content", [])
            text_parts = []
            for part in content_parts:
                if isinstance(part, dict):
                    text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)
            messages.append({"role": role, "content": "\n".join(text_parts)})
        elif item_type == "function_call":
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                }],
            })
        elif item_type == "function_call_output":
            # litellm 将 output 转为列表格式 [{"type": "input_text", "text": "..."}]
            # 需要从列表中提取文本，同时兼容字符串格式
            raw_output = item.get("output", "")
            if isinstance(raw_output, list):
                text_parts = []
                for part in raw_output:
                    if isinstance(part, dict):
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                output_text = "\n".join(text_parts)
            else:
                output_text = str(raw_output) if raw_output else ""
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": output_text,
            })

    # 转换 tools 格式（Responses API 扁平 → Chat Completions 嵌套）
    tools = None
    raw_tools = data.get("tools")
    if raw_tools:
        tools = []
        for t in raw_tools:
            if t.get("type") == "function":
                tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                })

    chat_data = {
        "model": data.get("model", "gpt-5.3-codex"),
        "messages": messages,
        "max_tokens": data.get("max_output_tokens", 8192),
        "temperature": data.get("temperature", 0.7),
    }
    if tools:
        chat_data["tools"] = tools
        chat_data["tool_choice"] = data.get("tool_choice", "auto")

    # 复用 handle_chat 获取 Chat Completions 格式响应
    chat_response = handle_chat(chat_data)
    chat_json = chat_response.get_json()

    # 将 Chat Completions 响应转为 Responses API 格式
    choice = (chat_json.get("choices") or [{}])[0]
    msg = choice.get("message", {})

    output = []
    # 处理 tool_calls
    for tc in msg.get("tool_calls", []):
        func = tc.get("function", {})
        output.append({
            "type": "function_call",
            "id": f"fc_{uuid.uuid4().hex[:8]}",
            "call_id": tc.get("id", ""),
            "name": func.get("name", ""),
            "arguments": func.get("arguments", "{}"),
            "status": "completed",
        })
    # 处理文本内容
    text = msg.get("content")
    if text:
        output.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:8]}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text}],
        })

    resp_id = f"resp_{uuid.uuid4().hex[:12]}"
    return jsonify({
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "model": chat_data["model"],
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    })


@app.route("/v1/models", methods=["GET"])
def models():
    """模型列表端点。"""
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": "gpt-5.3-codex",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "openai",
            },
            {
                "id": "gpt-5-codex",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "openai",
            },
        ],
    })


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Codex Bridge v2 — Chat Completions ↔ Responses API")
    print(f"   端口: {LISTEN_PORT}")
    print(f"   超时: {TIMEOUT_SECONDS}s")
    print(f"   认证: {AUTH_PATH}")
    print(f"   后端: {RESPONSES_API_URL}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=LISTEN_PORT)
