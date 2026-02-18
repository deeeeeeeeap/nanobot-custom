"""Jinja2-based prompt renderer for nanobot memory features."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined


_TEMPLATES_DIR = Path(__file__).parent / "templates"
_ENV = Environment(
    loader=None,
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

_TEMPLATE_FILES = {
    "memory_extraction": "memory_extraction.j2",
    "memory_merge": "memory_merge.j2",
    "structured_summary": "structured_summary.j2",
    "dedup_decision": "dedup_decision.j2",
}

_REQUIRED_VARS = {
    "memory_extraction": {"messages", "session_key", "output_language"},
    "memory_merge": {"existing_content", "new_content", "category", "output_language"},
    "structured_summary": {"messages", "session_key", "output_language"},
    "dedup_decision": {"candidate", "existing_memories", "output_language"},
}


def render_prompt(template_id: str, variables: dict[str, Any]) -> str:
    """Render a named prompt template with strict variable validation."""
    template_file = _TEMPLATE_FILES.get(template_id)
    if template_file is None:
        raise ValueError(f"Unknown prompt template: {template_id}")

    required = _REQUIRED_VARS.get(template_id, set())
    missing = [name for name in sorted(required) if name not in variables]
    if missing:
        raise ValueError(f"Missing required template variables for {template_id}: {', '.join(missing)}")

    template_path = _TEMPLATES_DIR / template_file
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    template_text = template_path.read_text(encoding="utf-8")
    template = _ENV.from_string(template_text)
    return template.render(**variables).strip()

