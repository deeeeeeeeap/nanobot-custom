"""Web tools: web_search and web_fetch.

Custom additions:
- result cache
- country/freshness filtering
- SSRF/network safety checks
- optional provider/fallback support
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from nanobot.agent.tools.base import Tool
from nanobot.security.network import validate_resolved_url, validate_url_target

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5

SEARCH_CACHE: dict[str, tuple[float, str]] = {}
CACHE_TTL_SECONDS = 300
FRESHNESS_VALUES = {"pd", "pw", "pm", "py"}


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL scheme and basic shape."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


def _validate_url_safe(url: str) -> tuple[bool, str]:
    """Validate URL with SSRF protections."""
    return validate_url_target(url)


async def _get_with_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.Response | None, str | None]:
    """GET a URL while validating every redirect target before requesting it."""
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        ok, error = validate_url_target(current_url)
        if not ok:
            return None, f"Redirect blocked: {error}"

        response = await client.get(current_url, headers=headers, follow_redirects=False)
        if not 300 <= response.status_code < 400:
            return response, None

        location = response.headers.get("location")
        if not location:
            return response, None

        current_url = urljoin(current_url, location)

    return None, f"Too many redirects: exceeded limit of {MAX_REDIRECTS}"


def _wrap_external_content(text: str, source: str = "web") -> str:
    """Strip control characters from external content."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def _get_cache_key(
    query: str,
    count: int,
    country: str | None,
    freshness: str | None,
    provider: str = "brave",
) -> str:
    """Build a cache key."""
    return f"{provider}:{query}:{count}:{country or 'default'}:{freshness or 'default'}"


def _read_cache(key: str) -> str | None:
    """Read a cached result if still fresh."""
    if key in SEARCH_CACHE:
        timestamp, result = SEARCH_CACHE[key]
        if time.time() - timestamp < CACHE_TTL_SECONDS:
            return result
        del SEARCH_CACHE[key]
    return None


def _write_cache(key: str, result: str) -> None:
    """Write a cached result and keep the cache bounded."""
    if len(SEARCH_CACHE) > 100:
        sorted_keys = sorted(SEARCH_CACHE.keys(), key=lambda k: SEARCH_CACHE[k][0])
        for old_key in sorted_keys[:50]:
            del SEARCH_CACHE[old_key]
    SEARCH_CACHE[key] = (time.time(), result)


def _normalize_freshness(value: str | None) -> str | None:
    """Normalize freshness values used by Brave Search."""
    if not value:
        return None
    trimmed = value.strip().lower()
    if trimmed in FRESHNESS_VALUES:
        return trimmed
    if re.match(r"^\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}$", trimmed):
        return trimmed
    return None


def _format_search_results(query: str, items: list[dict[str, Any]], n: int) -> str:
    """Format generic provider results."""
    if not items:
        return json.dumps({"query": query, "count": 0, "message": f"No results for '{query}'"})

    lines = [f"Results for: {query}\n"]
    for i, item in enumerate(items[:n], 1):
        title = _normalize(_strip_tags(str(item.get("title", ""))))
        url = str(item.get("url", ""))
        snippet = _normalize(
            _strip_tags(
                str(
                    item.get("content")
                    or item.get("description")
                    or item.get("body")
                    or ""
                )
            )
        )
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


class WebSearchTool(Tool):
    """Search the web using Brave or an optional fallback provider."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, snippets, and optional freshness/country filtering."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "count": {
                "type": "integer",
                "description": "Result count (1-10)",
                "minimum": 1,
                "maximum": 10,
            },
            "country": {
                "type": "string",
                "description": "Country/region code (e.g. US, CN, DE, ALL). Defaults to US.",
            },
            "freshness": {
                "type": "string",
                "description": "Freshness filter: pd, pw, pm, py, or YYYY-MM-DDtoYYYY-MM-DD",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 5,
        timeout: float = 15.0,
        provider: str | None = None,
        fallback_provider: str | None = None,
        proxy: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self.max_results = max_results
        self.timeout = timeout
        self.provider = (provider or os.environ.get("WEB_SEARCH_PROVIDER", "brave")).strip().lower() or "brave"
        self.fallback_provider = (fallback_provider or os.environ.get("WEB_SEARCH_FALLBACK_PROVIDER", "")).strip().lower()
        self.proxy = proxy

    async def execute(
        self,
        query: str,
        count: int | None = None,
        country: str | None = None,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> str:
        n = min(max(count or self.max_results, 1), 10)
        normalized_freshness = _normalize_freshness(freshness)
        cache_key = _get_cache_key(query, n, country, normalized_freshness, self.provider)

        cached = _read_cache(cache_key)
        if cached:
            return cached + "\n\n(cached)"

        ok, result = await self._search_provider(query, n, country, normalized_freshness, self.provider)
        if not ok and self.fallback_provider and self.fallback_provider != self.provider:
            fallback_ok, fallback_result = await self._search_provider(
                query,
                n,
                country,
                normalized_freshness,
                self.fallback_provider,
            )
            if fallback_ok:
                ok, result = True, fallback_result

        if ok:
            _write_cache(cache_key, result)
        return result

    async def _search_provider(
        self,
        query: str,
        n: int,
        country: str | None,
        freshness: str | None,
        provider: str,
    ) -> tuple[bool, str]:
        provider = provider.strip().lower()
        if provider == "brave":
            return await self._search_brave(query, n, country, freshness)
        if provider == "duckduckgo":
            return await self._search_duckduckgo(query, n)
        if provider == "tavily":
            return await self._search_tavily(query, n)
        if provider == "searxng":
            return await self._search_searxng(query, n)
        if provider == "jina":
            return await self._search_jina(query, n)
        return False, json.dumps({"error": "unknown_provider", "message": f"Unknown search provider '{provider}'"})

    async def _search_brave(
        self,
        query: str,
        n: int,
        country: str | None,
        freshness: str | None,
    ) -> tuple[bool, str]:
        api_key = self.api_key or os.environ.get("BRAVE_API_KEY", "")
        if not api_key:
            return False, json.dumps(
                {
                    "error": "missing_api_key",
                    "message": "BRAVE_API_KEY is not configured.",
                }
            )

        params: dict[str, Any] = {"q": query, "count": n}
        if country:
            params["country"] = country.upper()
        if freshness:
            params["freshness"] = freshness

        try:
            start_time = time.time()
            client_kwargs: dict[str, Any] = {}
            if self.proxy:
                client_kwargs["proxy"] = self.proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                    timeout=self.timeout,
                )
                r.raise_for_status()

            elapsed_ms = int((time.time() - start_time) * 1000)
            results = r.json().get("web", {}).get("results", [])
            if not results:
                return True, json.dumps(
                    {
                        "query": query,
                        "count": 0,
                        "message": f"No results found for '{query}'",
                    }
                )

            lines = [f"Search results: {query} (took {elapsed_ms}ms)\n"]
            for i, item in enumerate(results[:n], 1):
                title = _wrap_external_content(str(item.get("title", "")))
                url = str(item.get("url", ""))
                desc = _wrap_external_content(str(item.get("description", "")))
                age = str(item.get("age", ""))

                lines.append(f"{i}. {title}")
                lines.append(f"   {url}")
                if desc:
                    lines.append(f"   {desc}")
                if age:
                    lines.append(f"   Published: {age}")

            return True, "\n".join(lines)
        except httpx.TimeoutException:
            return False, json.dumps({"error": "timeout", "message": f"Search timed out ({self.timeout}s)"})
        except httpx.HTTPStatusError as e:
            return False, json.dumps(
                {"error": "http_error", "status": e.response.status_code, "message": str(e)}
            )
        except Exception as e:
            return False, json.dumps({"error": "unknown", "message": str(e)})

    async def _search_duckduckgo(self, query: str, n: int) -> tuple[bool, str]:
        try:
            from ddgs import DDGS
        except Exception as e:
            return False, json.dumps({"error": "missing_dependency", "message": str(e)})

        try:
            ddgs = DDGS(timeout=10)
            raw = await asyncio.to_thread(ddgs.text, query, max_results=n)
            if not raw:
                return True, json.dumps({"query": query, "count": 0, "message": f"No results for '{query}'"})

            items = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "content": r.get("body", ""),
                }
                for r in raw
            ]
            return True, _format_search_results(query, items, n)
        except Exception as e:
            return False, json.dumps({"error": "duckduckgo_error", "message": str(e)})

    async def _search_tavily(self, query: str, n: int) -> tuple[bool, str]:
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            return False, json.dumps({"error": "missing_api_key", "message": "TAVILY_API_KEY is not configured."})

        try:
            client_kwargs: dict[str, Any] = {}
            if self.proxy:
                client_kwargs["proxy"] = self.proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"query": query, "max_results": n},
                    timeout=15.0,
                )
                r.raise_for_status()
            return True, _format_search_results(query, r.json().get("results", []), n)
        except Exception as e:
            return False, json.dumps({"error": "tavily_error", "message": str(e)})

    async def _search_searxng(self, query: str, n: int) -> tuple[bool, str]:
        base_url = (os.environ.get("SEARXNG_BASE_URL", "")).strip()
        if not base_url:
            return False, json.dumps({"error": "missing_base_url", "message": "SEARXNG_BASE_URL is not configured."})

        endpoint = f"{base_url.rstrip('/')}/search"
        is_valid, error_msg = _validate_url_safe(endpoint)
        if not is_valid:
            return False, json.dumps({"error": "invalid_url", "message": error_msg})

        try:
            client_kwargs: dict[str, Any] = {}
            if self.proxy:
                client_kwargs["proxy"] = self.proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                r = await client.get(
                    endpoint,
                    params={"q": query, "format": "json"},
                    headers={"User-Agent": USER_AGENT},
                    timeout=10.0,
                )
                r.raise_for_status()
            return True, _format_search_results(query, r.json().get("results", []), n)
        except Exception as e:
            return False, json.dumps({"error": "searxng_error", "message": str(e)})

    async def _search_jina(self, query: str, n: int) -> tuple[bool, str]:
        api_key = os.environ.get("JINA_API_KEY", "")
        try:
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            client_kwargs: dict[str, Any] = {}
            if self.proxy:
                client_kwargs["proxy"] = self.proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                r = await client.get(
                    "https://s.jina.ai/",
                    params={"q": query},
                    headers=headers,
                    timeout=15.0,
                )
                r.raise_for_status()

            payload = r.json()
            data = payload.get("data", [])
            if isinstance(data, dict):
                data = [data]

            items = [
                {
                    "title": d.get("title", ""),
                    "url": d.get("url", ""),
                    "content": d.get("content", ""),
                }
                for d in data[:n]
            ]
            return True, _format_search_results(query, items, n)
        except Exception as e:
            return False, json.dumps({"error": "jina_error", "message": str(e)})


class WebFetchTool(Tool):
    """Fetch a URL and extract readable content."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML -> markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100, "description": "Maximum character count"},
        },
        "required": ["url"],
    }

    def __init__(
        self,
        max_chars: int = 50000,
        provider: str | None = None,
        fallback_provider: str | None = None,
        proxy: str | None = None,
    ):
        self.max_chars = max_chars
        self.provider = (provider or os.environ.get("WEB_FETCH_PROVIDER", "readability")).strip().lower() or "readability"
        self.fallback_provider = (
            fallback_provider or os.environ.get("WEB_FETCH_FALLBACK_PROVIDER", "")
        ).strip().lower()
        self.proxy = proxy

    async def execute(
        self,
        url: str,
        extractMode: str = "markdown",
        maxChars: int | None = None,
        **kwargs: Any,
    ) -> str:
        max_chars = maxChars or self.max_chars

        is_valid, error_msg = _validate_url_safe(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url})

        provider = self.provider
        if provider in {"jina", "auto"}:
            jina_result = await self._fetch_jina(url, max_chars)
            if jina_result is not None:
                return jina_result
            provider = self.fallback_provider or "readability"

        if provider not in {"readability", "", "local"}:
            if self.fallback_provider in {"readability", "local"}:
                provider = "readability"
            else:
                return json.dumps({"error": "unknown_provider", "message": f"Unknown fetch provider '{provider}'", "url": url})

        return await self._fetch_readability(url, extractMode, max_chars)

    async def _fetch_jina(self, url: str, max_chars: int) -> str | None:
        """Try Jina Reader first. Returns None on failure so the caller can fall back."""
        try:
            headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
            jina_key = os.environ.get("JINA_API_KEY", "")
            if jina_key:
                headers["Authorization"] = f"Bearer {jina_key}"

            client_kwargs: dict[str, Any] = {}
            if self.proxy:
                client_kwargs["proxy"] = self.proxy
            async with httpx.AsyncClient(timeout=20.0, **client_kwargs) as client:
                r = await client.get(f"https://r.jina.ai/{url}", headers=headers)
                if r.status_code == 429:
                    return None
                r.raise_for_status()

            data = r.json().get("data", {})
            title = data.get("title", "")
            text = data.get("content", "")
            if not text:
                return None

            if title:
                text = f"# {title}\n\n{text}"
            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps(
                {
                    "url": url,
                    "finalUrl": data.get("url", url),
                    "status": r.status_code,
                    "extractor": "jina",
                    "truncated": truncated,
                    "length": len(text),
                    "text": _wrap_external_content(text),
                }
            )
        except Exception:
            return None

    async def _fetch_readability(self, url: str, extract_mode: str, max_chars: int) -> str:
        """Fetch locally and extract with readability-lxml."""
        from readability import Document

        try:
            client_kwargs: dict[str, Any] = {
                "timeout": 30.0,
            }
            if self.proxy:
                client_kwargs["proxy"] = self.proxy

            async with httpx.AsyncClient(**client_kwargs) as client:
                r, redirect_error = await _get_with_safe_redirects(
                    client,
                    url,
                    headers={"User-Agent": USER_AGENT},
                )
                if redirect_error:
                    return json.dumps({"error": redirect_error, "url": url})
                if r is None:
                    return json.dumps({"error": "Request failed before response", "url": url})
                r.raise_for_status()

            redir_ok, redir_err = validate_resolved_url(str(r.url))
            if not redir_ok:
                return json.dumps({"error": f"Redirect blocked: {redir_err}", "url": url})

            ctype = r.headers.get("content-type", "")
            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2), "json"
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                content = self._to_markdown(doc.summary()) if extract_mode == "markdown" else _strip_tags(doc.summary())
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = r.text, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps(
                {
                    "url": url,
                    "finalUrl": str(r.url),
                    "status": r.status_code,
                    "extractor": extractor,
                    "truncated": truncated,
                    "length": len(text),
                    "text": _wrap_external_content(text),
                }
            )
        except httpx.ProxyError as e:
            return json.dumps({"error": f"Proxy error: {e}", "url": url})
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})

    def _to_markdown(self, html_content: str) -> str:
        """Convert HTML to markdown-like text."""
        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            lambda m: f"[{_strip_tags(m[2])}]({m[1]})",
            html_content,
            flags=re.I,
        )
        text = re.sub(
            r"<h([1-6])[^>]*>([\s\S]*?)</h\1>",
            lambda m: f"\n{'#' * int(m[1])} {_strip_tags(m[2])}\n",
            text,
            flags=re.I,
        )
        text = re.sub(r"<li[^>]*>([\s\S]*?)</li>", lambda m: f"\n- {_strip_tags(m[1])}", text, flags=re.I)
        text = re.sub(r"</(p|div|section|article)>", "\n\n", text, flags=re.I)
        text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.I)
        return _normalize(_strip_tags(text))
