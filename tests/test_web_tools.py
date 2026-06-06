import builtins
import json

import httpx

from nanobot.agent.tools.web import WebFetchTool, WebSearchTool


async def test_duckduckgo_missing_dependency_returns_install_hint(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ddgs":
            raise ImportError("ddgs missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    ok, result = await WebSearchTool(provider="duckduckgo")._search_duckduckgo("nanobot", 3)

    assert ok is False
    payload = json.loads(result)
    assert payload["error"] == "missing_dependency"
    assert "pip install -e '.[duckduckgo]'" in payload["message"]


async def test_jina_fetch_auth_failure_with_key_returns_explicit_error(monkeypatch) -> None:
    monkeypatch.setenv("JINA_API_KEY", "jina-secret-key")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, headers):
            return httpx.Response(
                401,
                json={"error": "unauthorized"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("nanobot.agent.tools.web.httpx.AsyncClient", FakeClient)

    result = await WebFetchTool(provider="jina")._fetch_jina("https://example.com", 1000)

    assert result is not None
    payload = json.loads(result)
    assert payload["error"] == "jina_auth_error"
    assert payload["status"] == 401
    assert "jina-secret-key" not in result


async def test_jina_fetch_rate_limit_allows_fallback(monkeypatch) -> None:
    monkeypatch.setenv("JINA_API_KEY", "jina-secret-key")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, headers):
            return httpx.Response(
                429,
                text="rate limited",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("nanobot.agent.tools.web.httpx.AsyncClient", FakeClient)

    assert await WebFetchTool(provider="jina")._fetch_jina("https://example.com", 1000) is None
