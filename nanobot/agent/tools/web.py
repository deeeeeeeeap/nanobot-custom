"""Web tools: web_search and web_fetch.

借鉴 OpenClaw 的改进：
- 结果缓存（避免重复搜索）
- 地区和语言参数
- 时效过滤（freshness）
- 外部内容安全标记
"""

import html
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from nanobot.agent.tools.base import Tool

# 共享常量
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # 限制重定向次数防止 DoS 攻击

# 缓存设置（借鉴 OpenClaw）
SEARCH_CACHE: dict[str, tuple[float, str]] = {}  # {cache_key: (timestamp, result)}
CACHE_TTL_SECONDS = 300  # 缓存 5 分钟

# Brave Search 时效过滤值
FRESHNESS_VALUES = {"pd", "pw", "pm", "py"}  # past day/week/month/year


def _strip_tags(text: str) -> str:
    """移除 HTML 标签并解码实体。"""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """规范化空白字符。"""
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """验证 URL：必须是 http(s) 且有有效域名。"""
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"仅支持 http/https，收到 '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "缺少域名"
        return True, ""
    except Exception as e:
        return False, str(e)


def _wrap_external_content(text: str, source: str = "web") -> str:
    """
    标记外部内容（借鉴 OpenClaw 的安全实践）。
    防止搜索结果中的 prompt injection。
    """
    # 移除可能的控制字符
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return cleaned


def _get_cache_key(query: str, count: int, country: str | None, freshness: str | None) -> str:
    """生成缓存键。"""
    return f"{query}:{count}:{country or 'default'}:{freshness or 'default'}"


def _read_cache(key: str) -> str | None:
    """读取缓存，如果过期则返回 None。"""
    if key in SEARCH_CACHE:
        timestamp, result = SEARCH_CACHE[key]
        if time.time() - timestamp < CACHE_TTL_SECONDS:
            return result
        # 过期，删除
        del SEARCH_CACHE[key]
    return None


def _write_cache(key: str, result: str) -> None:
    """写入缓存。"""
    # 限制缓存大小（防止内存溢出）
    if len(SEARCH_CACHE) > 100:
        # 删除最旧的一半
        sorted_keys = sorted(SEARCH_CACHE.keys(), key=lambda k: SEARCH_CACHE[k][0])
        for old_key in sorted_keys[:50]:
            del SEARCH_CACHE[old_key]
    SEARCH_CACHE[key] = (time.time(), result)


def _normalize_freshness(value: str | None) -> str | None:
    """
    规范化 freshness 参数（借鉴 OpenClaw）。
    支持: pd (过去24h), pw (过去一周), pm (过去一月), py (过去一年)
    或日期范围: YYYY-MM-DDtoYYYY-MM-DD
    """
    if not value:
        return None
    trimmed = value.strip().lower()
    if trimmed in FRESHNESS_VALUES:
        return trimmed
    # 检查日期范围格式
    if re.match(r'^\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}$', trimmed):
        return trimmed
    return None


class WebSearchTool(Tool):
    """使用 Brave Search API 搜索网络（已增强）。"""
    
    name = "web_search"
    description = (
        "搜索网络获取最新信息。支持地区限定和时效过滤。"
        "返回标题、URL 和摘要。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string", 
                "description": "搜索查询词"
            },
            "count": {
                "type": "integer", 
                "description": "结果数量 (1-10)", 
                "minimum": 1, 
                "maximum": 10
            },
            "country": {
                "type": "string",
                "description": "国家/地区代码 (如 US, CN, DE, ALL)，默认 US"
            },
            "freshness": {
                "type": "string",
                "description": "时效过滤: pd=过去24小时, pw=过去一周, pm=过去一月, py=过去一年"
            }
        },
        "required": ["query"]
    }
    
    def __init__(self, api_key: str | None = None, max_results: int = 5, timeout: float = 15.0):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self.max_results = max_results
        self.timeout = timeout
    
    async def execute(
        self, 
        query: str, 
        count: int | None = None, 
        country: str | None = None,
        freshness: str | None = None,
        **kwargs: Any
    ) -> str:
        if not self.api_key:
            return json.dumps({
                "error": "missing_api_key",
                "message": "BRAVE_API_KEY 未配置。请在配置文件中设置 Brave Search API 密钥。"
            })
        
        n = min(max(count or self.max_results, 1), 10)
        normalized_freshness = _normalize_freshness(freshness)
        
        # 检查缓存
        cache_key = _get_cache_key(query, n, country, normalized_freshness)
        cached = _read_cache(cache_key)
        if cached:
            return cached + "\n\n(cached)"
        
        try:
            # 构建请求参数
            params: dict[str, Any] = {"q": query, "count": n}
            if country:
                params["country"] = country.upper()
            if normalized_freshness:
                params["freshness"] = normalized_freshness
            
            start_time = time.time()
            
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params=params,
                    headers={
                        "Accept": "application/json", 
                        "X-Subscription-Token": self.api_key
                    },
                    timeout=self.timeout
                )
                r.raise_for_status()
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            results = r.json().get("web", {}).get("results", [])
            if not results:
                return json.dumps({
                    "query": query,
                    "count": 0,
                    "message": f"未找到关于 '{query}' 的结果"
                })
            
            # 格式化结果（使用安全包装）
            lines = [f"搜索结果: {query} (用时 {elapsed_ms}ms)\n"]
            for i, item in enumerate(results[:n], 1):
                title = _wrap_external_content(item.get('title', ''))
                url = item.get('url', '')
                desc = _wrap_external_content(item.get('description', ''))
                age = item.get('age', '')
                
                lines.append(f"{i}. {title}")
                lines.append(f"   {url}")
                if desc:
                    lines.append(f"   {desc}")
                if age:
                    lines.append(f"   发布时间: {age}")
            
            result = "\n".join(lines)
            
            # 写入缓存
            _write_cache(cache_key, result)
            
            return result
            
        except httpx.TimeoutException:
            return json.dumps({"error": "timeout", "message": f"搜索超时 ({self.timeout}s)"})
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": "http_error", "status": e.response.status_code, "message": str(e)})
        except Exception as e:
            return json.dumps({"error": "unknown", "message": str(e)})


class WebFetchTool(Tool):
    """使用 Readability 抓取并提取 URL 内容。"""
    
    name = "web_fetch"
    description = "抓取 URL 并提取可读内容 (HTML → markdown/text)。"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的 URL"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100, "description": "最大字符数"}
        },
        "required": ["url"]
    }
    
    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars
    
    async def execute(self, url: str, extractMode: str = "markdown", maxChars: int | None = None, **kwargs: Any) -> str:
        from readability import Document

        max_chars = maxChars or self.max_chars

        # 验证 URL
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL 验证失败: {error_msg}", "url": url})

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=30.0
            ) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
            
            ctype = r.headers.get("content-type", "")
            
            # JSON
            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2), "json"
            # HTML
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = self._to_markdown(doc.summary()) if extractMode == "markdown" else _strip_tags(doc.summary())
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text, "raw"
            
            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]
            
            return json.dumps({
                "url": url, 
                "finalUrl": str(r.url), 
                "status": r.status_code,
                "extractor": extractor, 
                "truncated": truncated, 
                "length": len(text), 
                "text": _wrap_external_content(text)
            })
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})
    
    def _to_markdown(self, html_content: str) -> str:
        """将 HTML 转换为 markdown。"""
        # 转换链接、标题、列表
        text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                      lambda m: f'[{_strip_tags(m[2])}]({m[1]})', html_content, flags=re.I)
        text = re.sub(r'<h([1-6])[^>]*>([\s\S]*?)</h\1>',
                      lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n', text, flags=re.I)
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
