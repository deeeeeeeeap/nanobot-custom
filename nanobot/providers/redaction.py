"""Provider error redaction helpers."""

from __future__ import annotations

import re

_KV_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)"
)
_JSON_SECRET_RE = re.compile(
    r"(?i)([\"'])(api[_-]?key|token|password|secret|authorization)\1\s*:\s*([\"'])(.*?)\3"
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{12,}\b")
_LONG_HEX_RE = re.compile(r"\b[a-fA-F0-9]{48,}\b")


def redact_provider_error(text: object, *, max_chars: int | None = None) -> str:
    """Redact likely credentials from provider-facing error text."""
    normalized = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if not normalized:
        return ""
    redacted = _JSON_SECRET_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}:{m.group(3)}[REDACTED]{m.group(3)}",
        normalized,
    )
    redacted = _AUTH_HEADER_RE.sub("Authorization: Bearer [REDACTED]", redacted)
    redacted = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", redacted)
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _TELEGRAM_TOKEN_RE.sub("[REDACTED]", redacted)
    redacted = _LONG_HEX_RE.sub("[REDACTED]", redacted)
    if max_chars is not None and len(redacted) > max_chars:
        return f"{redacted[:max_chars]}..."
    return redacted
