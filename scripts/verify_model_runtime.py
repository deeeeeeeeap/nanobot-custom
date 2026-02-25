#!/usr/bin/env python3
"""Verify whether nanobot is really using the expected chat model at runtime.

Usage examples (VPS):
  .venv/bin/python scripts/verify_model_runtime.py --expected-model openai/gpt-5.3-codex
  .venv/bin/python scripts/verify_model_runtime.py --bridge-journal-unit codex-bridge
  .venv/bin/python scripts/verify_model_runtime.py --bridge-log /var/log/codex_bridge.log
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUEST_RE = re.compile(
    r"LLM request: model=(?P<model>[^,]+),\s*tools=(?P<tools>[^,]+),\s*tool_choice="
    r"(?P<tool_choice>[^,]+),\s*api_base=(?P<api_base>\S+)"
)
FALLBACK_RE = re.compile(
    r"Using fallback model for this request:\s*(?P<from>\S+)\s*->\s*(?P<to>\S+)"
)
BRIDGE_RE = re.compile(r"(?:请求|Request):\s*model=(?P<model>[^,\s]+)")


@dataclass
class RequestEntry:
    model: str
    tools: str
    tool_choice: str
    api_base: str


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if max_lines <= 0:
        return lines
    return lines[-max_lines:]


def _load_config_model() -> dict[str, Any]:
    data = {
        "model": None,
        "provider": None,
        "api_base": None,
        "fallbacks": [],
        "source": "unknown",
    }

    try:
        from nanobot.config.loader import load_config  # type: ignore

        cfg = load_config()
        model = cfg.agents.defaults.model
        data["model"] = model
        data["provider"] = cfg.get_provider_name(model)
        data["api_base"] = cfg.get_api_base(model)
        data["fallbacks"] = list(cfg.agents.defaults.model_fallbacks)
        data["source"] = "nanobot.config.loader"
        return data
    except Exception:
        pass

    config_path = Path.home() / ".nanobot" / "config.json"
    if not config_path.exists():
        return data

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        defaults = raw.get("agents", {}).get("defaults", {})
        data["model"] = defaults.get("model")
        data["fallbacks"] = defaults.get("modelFallbacks", []) or []
        data["source"] = str(config_path)
    except Exception:
        pass
    return data


def _parse_runtime_requests(lines: list[str]) -> list[RequestEntry]:
    entries: list[RequestEntry] = []
    for line in lines:
        m = REQUEST_RE.search(line)
        if not m:
            continue
        entries.append(
            RequestEntry(
                model=m.group("model").strip(),
                tools=m.group("tools").strip(),
                tool_choice=m.group("tool_choice").strip(),
                api_base=m.group("api_base").strip(),
            )
        )
    return entries


def _parse_fallbacks(lines: list[str]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for line in lines:
        m = FALLBACK_RE.search(line)
        if m:
            items.append((m.group("from"), m.group("to")))
    return items


def _read_bridge_lines(args: argparse.Namespace) -> list[str]:
    if args.bridge_log:
        return _tail_lines(Path(args.bridge_log).expanduser(), args.tail_lines)

    if args.bridge_journal_unit:
        cmd = [
            "journalctl",
            "-u",
            args.bridge_journal_unit,
            "-n",
            str(args.tail_lines),
            "--no-pager",
        ]
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
            if proc.returncode != 0:
                return []
            return proc.stdout.splitlines()
        except Exception:
            return []
    return []


def _parse_bridge_models(lines: list[str]) -> list[str]:
    models: list[str] = []
    for line in lines:
        m = BRIDGE_RE.search(line)
        if m:
            models.append(m.group("model").strip())
    return models


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify runtime model for nanobot/codex bridge.")
    parser.add_argument(
        "--expected-model",
        default="openai/gpt-5.3-codex",
        help="Expected model id in nanobot runtime requests.",
    )
    parser.add_argument(
        "--log-file",
        default=str(Path.home() / ".nanobot" / "logs" / "nanobot.log"),
        help="nanobot log file path.",
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=600,
        help="How many latest log lines to inspect.",
    )
    parser.add_argument(
        "--bridge-log",
        default="",
        help="Optional bridge log file path (contains '请求: model=...').",
    )
    parser.add_argument(
        "--bridge-journal-unit",
        default="",
        help="Optional systemd unit for bridge, e.g. codex-bridge.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require both nanobot runtime and bridge latest model to match expected model.",
    )
    args = parser.parse_args()

    expected = args.expected_model.strip()
    cfg = _load_config_model()
    print("=== Config ===")
    print(f"source      : {cfg['source']}")
    print(f"model       : {cfg['model']}")
    print(f"provider    : {cfg['provider']}")
    print(f"api_base    : {cfg['api_base']}")
    print(f"fallbacks   : {cfg['fallbacks']}")
    print(f"expected    : {expected}")
    print()

    log_path = Path(args.log_file).expanduser()
    lines = _tail_lines(log_path, args.tail_lines)
    runtime_entries = _parse_runtime_requests(lines)
    fallbacks = _parse_fallbacks(lines)

    print("=== Runtime (nanobot.log) ===")
    print(f"log_file    : {log_path}")
    print(f"log_exists  : {log_path.exists()}")
    print(f"entries     : {len(runtime_entries)}")
    if runtime_entries:
        latest = runtime_entries[-1]
        unique_models = sorted({e.model for e in runtime_entries})
        print(f"latest_model: {latest.model}")
        print(f"latest_base : {latest.api_base}")
        print(f"tool_choice : {latest.tool_choice}")
        print(f"models_seen : {unique_models}")
    else:
        latest = None
    print(f"fallbacks   : {fallbacks[-5:] if fallbacks else []}")
    print()

    bridge_lines = _read_bridge_lines(args)
    bridge_models = _parse_bridge_models(bridge_lines)
    print("=== Bridge ===")
    if args.bridge_log:
        print(f"bridge_log  : {Path(args.bridge_log).expanduser()}")
    elif args.bridge_journal_unit:
        print(f"journal_unit: {args.bridge_journal_unit}")
    else:
        print("bridge_input: (not provided)")
    print(f"bridge_hits : {len(bridge_models)}")
    if bridge_models:
        print(f"bridge_last : {bridge_models[-1]}")
        print(f"bridge_seen : {sorted(set(bridge_models))}")
    print()

    config_ok = cfg["model"] == expected
    runtime_ok = bool(latest and latest.model == expected)
    bridge_ok = True if not bridge_models else (bridge_models[-1] == expected)

    print("=== Verdict ===")
    print(f"config_ok   : {config_ok}")
    print(f"runtime_ok  : {runtime_ok}")
    print(f"bridge_ok   : {bridge_ok}")
    print(f"fallback_on : {bool(fallbacks)}")

    if args.strict:
        ok = config_ok and runtime_ok and bridge_ok and not fallbacks
    else:
        ok = config_ok and runtime_ok and bridge_ok

    if not ok:
        print("\nResult: FAIL")
        if not config_ok:
            print("- 配置模型与预期不一致。")
        if not runtime_ok:
            print("- nanobot 运行日志中的最新请求模型不是预期模型。")
        if bridge_models and not bridge_ok:
            print("- bridge 收到的最新模型不是预期模型。")
        if fallbacks:
            print("- 检测到 failover 发生，体感可能偏离主模型。")
        return 2

    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
