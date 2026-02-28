"""Codex auth/token management for native Responses API access."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_REFRESH_URL = "https://auth.openai.com/oauth/token"


@dataclass
class CodexTokens:
    access_token: str = ""
    refresh_token: str = ""
    account_id: str = ""


class CodexAuth:
    """Load, refresh, and persist Codex CLI tokens."""

    def __init__(
        self,
        *,
        codex_home: str | None = None,
        auth_path: str | None = None,
        refresh_url: str = _CODEX_REFRESH_URL,
        max_age_seconds: int = 3600,
        refresh_margin_seconds: int = 300,
    ) -> None:
        self.auth_path = self._resolve_auth_path(codex_home=codex_home, auth_path=auth_path)
        self.refresh_url = refresh_url
        self.max_age_seconds = max(300, max_age_seconds)
        self.refresh_margin_seconds = max(60, min(refresh_margin_seconds, self.max_age_seconds - 60))
        self._lock = asyncio.Lock()
        self._tokens = CodexTokens()
        self.load()

    @staticmethod
    def _resolve_auth_path(*, codex_home: str | None, auth_path: str | None) -> Path:
        if auth_path:
            return Path(auth_path).expanduser()
        env_auth_path = os.environ.get("CODEX_AUTH_PATH")
        if env_auth_path:
            return Path(env_auth_path).expanduser()
        if codex_home:
            return Path(codex_home).expanduser() / "auth.json"
        env_codex_home = os.environ.get("CODEX_HOME")
        if env_codex_home:
            return Path(env_codex_home).expanduser() / "auth.json"
        return Path.home() / ".codex" / "auth.json"

    @property
    def tokens(self) -> CodexTokens:
        return self._tokens

    def load(self) -> CodexTokens:
        """Load auth.json and extract token fields."""
        if not self.auth_path.exists():
            self._tokens = CodexTokens()
            return self._tokens

        try:
            data = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read Codex auth file {}: {}", self.auth_path, e)
            self._tokens = CodexTokens()
            return self._tokens

        tokens_blob = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        access_token = str(tokens_blob.get("access_token") or data.get("access_token") or "").strip()
        refresh_token = str(tokens_blob.get("refresh_token") or data.get("refresh_token") or "").strip()
        account_id = str(tokens_blob.get("account_id") or data.get("account_id") or "").strip()
        self._tokens = CodexTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            account_id=account_id,
        )
        return self._tokens

    def _auth_mtime(self) -> float:
        try:
            return self.auth_path.stat().st_mtime
        except OSError:
            return 0.0

    def is_expired(self) -> bool:
        """Treat token as expired at mtime + 1h, with early refresh margin."""
        if not self._tokens.access_token:
            return True
        mtime = self._auth_mtime()
        if mtime <= 0:
            return True
        age = max(0.0, time.time() - mtime)
        return age >= (self.max_age_seconds - self.refresh_margin_seconds)

    async def ensure_valid(self, force: bool = False) -> None:
        """Ensure access token is available and refreshed when needed."""
        if not force and not self.is_expired():
            return

        async with self._lock:
            self.load()
            if not force and not self.is_expired():
                return

            refreshed = await self._refresh_via_http()
            if not refreshed:
                refreshed = await self._refresh_via_cli()
            if refreshed:
                self.load()
            if not self._tokens.access_token:
                raise RuntimeError(
                    "Codex token unavailable. Run `codex auth` or `codex auth refresh` on the host."
                )

    async def _refresh_via_http(self) -> bool:
        refresh_token = self._tokens.refresh_token
        if not refresh_token:
            return False

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CODEX_CLIENT_ID,
        }
        headers = {"Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self.refresh_url, json=payload, headers=headers)
            if response.status_code != 200:
                logger.warning("Codex HTTP refresh failed with status {}", response.status_code)
                return False
            data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
            logger.warning("Codex HTTP refresh failed: {}", e)
            return False

        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            logger.warning("Codex HTTP refresh response missing access_token")
            return False
        refresh_token_new = str(data.get("refresh_token") or "").strip()
        account_id = str(data.get("account_id") or self._tokens.account_id).strip()
        self._tokens = CodexTokens(
            access_token=access_token,
            refresh_token=refresh_token_new or self._tokens.refresh_token,
            account_id=account_id,
        )
        await self._save()
        logger.info("Codex token refreshed via HTTP endpoint")
        return True

    async def _refresh_via_cli(self) -> bool:
        """Fallback refresh path via codex CLI subprocess."""
        commands = (
            ["codex", "auth", "refresh"],
            ["codex", "auth"],
        )
        for cmd in commands:
            code, stdout, stderr = await asyncio.to_thread(self._run_cli_command, cmd)
            if code != 0:
                logger.warning(
                    "Codex CLI refresh command failed: {} (stderr={})",
                    " ".join(cmd),
                    stderr.strip()[:240],
                )
                continue
            self.load()
            if self._tokens.access_token:
                logger.info("Codex token refreshed via CLI command: {}", " ".join(cmd))
                return True
            logger.warning(
                "Codex CLI command succeeded but auth.json still has no access_token: {} (stdout={})",
                " ".join(cmd),
                stdout.strip()[:240],
            )
        return False

    @staticmethod
    def _run_cli_command(cmd: list[str]) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except OSError as e:
            return 1, "", str(e)

    async def _save(self) -> None:
        await asyncio.to_thread(self._save_sync)

    def _save_sync(self) -> None:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if self.auth_path.exists():
            try:
                data = json.loads(self.auth_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}

        tokens_blob = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        tokens_blob["access_token"] = self._tokens.access_token
        if self._tokens.refresh_token:
            tokens_blob["refresh_token"] = self._tokens.refresh_token
        if self._tokens.account_id:
            tokens_blob["account_id"] = self._tokens.account_id
        data["tokens"] = tokens_blob
        data["access_token"] = self._tokens.access_token
        if self._tokens.refresh_token:
            data["refresh_token"] = self._tokens.refresh_token
        if self._tokens.account_id:
            data["account_id"] = self._tokens.account_id

        tmp_path = self.auth_path.with_suffix(self.auth_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.auth_path)

    def get_headers(self) -> dict[str, str]:
        """Return auth headers for Responses API requests."""
        if not self._tokens.access_token:
            raise RuntimeError("Codex access token is missing")
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
        }
        if self._tokens.account_id:
            headers["ChatGPT-Account-Id"] = self._tokens.account_id
        return headers
