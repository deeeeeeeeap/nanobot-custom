#!/usr/bin/env python3
"""VPS smoke test for native Codex provider path."""

from __future__ import annotations

import argparse
import asyncio
import sys

from nanobot.providers.codex_auth import CodexAuth
from nanobot.providers.codex_provider import CodexProvider


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test Codex auth refresh and chat request.")
    parser.add_argument("--codex-home", default="", help="Override CODEX_HOME path")
    parser.add_argument("--auth-path", default="", help="Override auth.json path")
    parser.add_argument("--model", default="gpt-5.3-codex", help="Codex model name")
    parser.add_argument("--timeout", type=int, default=120, help="Request timeout seconds")
    parser.add_argument("--refresh", action="store_true", help="Force refresh before request")
    parser.add_argument("--request", action="store_true", help="Send one real chat request")
    parser.add_argument(
        "--message",
        default="请回复: smoke test ok",
        help="User message when --request is enabled",
    )
    parser.add_argument(
        "--responses-url",
        default="",
        help="Optional custom Responses API URL (for local bridge testing)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    auth = CodexAuth(
        codex_home=args.codex_home or None,
        auth_path=args.auth_path or None,
    )
    print(f"auth_path: {auth.auth_path}")
    print(f"has_access_token: {bool(auth.tokens.access_token)}")
    print(f"has_refresh_token: {bool(auth.tokens.refresh_token)}")
    print(f"account_id: {auth.tokens.account_id or '(empty)'}")

    try:
        await auth.ensure_valid(force=args.refresh)
    except Exception as e:
        print(f"AUTH_FAIL: {e}")
        return 2

    print("AUTH_OK")

    if not args.request:
        return 0

    provider = CodexProvider(
        default_model=args.model,
        codex_home=args.codex_home or None,
        timeout=args.timeout,
        auth=auth,
        responses_url=args.responses_url or None,
    )
    response = await provider.chat(
        messages=[{"role": "user", "content": args.message}],
        model=args.model,
    )
    print(f"finish_reason: {response.finish_reason}")
    print(f"tool_calls: {len(response.tool_calls)}")
    print(f"error_type: {response.error_type or ''}")
    print(f"content: {(response.content or '')[:400]}")
    return 0 if response.finish_reason != "error" else 3


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
