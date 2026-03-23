"""Utilities for cache-prefix fingerprinting and diffing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint_prefix(
    *,
    model: str,
    system_prompt: str,
    tool_definitions: list[dict[str, Any]] | None,
    bootstrap_text: str,
    skills_summary: str,
) -> dict[str, str]:
    """Build a stable fingerprint for the cache-relevant request prefix."""
    return {
        "model": model,
        "system_prompt_hash": _hash_text(system_prompt),
        "bootstrap_hash": _hash_text(bootstrap_text),
        "skills_summary_hash": _hash_text(skills_summary),
        "tool_schema_hash": _hash_text(_stable_json(tool_definitions or [])),
    }


def diff_fingerprint(previous: dict[str, str] | None, current: dict[str, str]) -> list[str]:
    """Return the list of fingerprint parts that changed since the previous call."""
    if not previous:
        return list(current.keys())
    changed: list[str] = []
    for key, value in current.items():
        if previous.get(key) != value:
            changed.append(key)
    return changed
