import json
from pathlib import Path

from nanobot.providers.codex_auth import CodexAuth


def _write_auth(path: Path, access: str, refresh: str, account: str) -> None:
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": access,
                    "refresh_token": refresh,
                    "account_id": account,
                }
            }
        ),
        encoding="utf-8",
    )


def test_codex_auth_load_and_headers(monkeypatch, tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, "access-old", "refresh-old", "acct-1")
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    auth = CodexAuth()
    headers = auth.get_headers()

    assert headers["Authorization"] == "Bearer access-old"
    assert headers["ChatGPT-Account-Id"] == "acct-1"


async def test_codex_auth_refresh_via_http_updates_file(monkeypatch, tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, "access-old", "refresh-old", "acct-1")
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "account_id": "acct-2",
            }

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(
        "nanobot.providers.codex_auth.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeClient(),
    )

    auth = CodexAuth()
    await auth.ensure_valid(force=True)

    reloaded = json.loads(auth_path.read_text(encoding="utf-8"))
    assert reloaded["tokens"]["access_token"] == "access-new"
    assert reloaded["tokens"]["refresh_token"] == "refresh-new"
    assert reloaded["tokens"]["account_id"] == "acct-2"


async def test_codex_auth_falls_back_to_cli_refresh(monkeypatch, tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, "access-old", "refresh-old", "acct-1")
    monkeypatch.setenv("CODEX_AUTH_PATH", str(auth_path))

    auth = CodexAuth()

    async def _http_fail():
        return False

    monkeypatch.setattr(auth, "_refresh_via_http", _http_fail)

    def _fake_run_cli(cmd):
        _write_auth(auth_path, "access-cli", "refresh-cli", "acct-cli")
        return 0, "ok", ""

    monkeypatch.setattr(auth, "_run_cli_command", _fake_run_cli)
    await auth.ensure_valid(force=True)

    assert auth.tokens.access_token == "access-cli"
    assert auth.tokens.refresh_token == "refresh-cli"
    assert auth.tokens.account_id == "acct-cli"
