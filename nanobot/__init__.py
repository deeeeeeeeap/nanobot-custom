"""
nanobot - A lightweight AI agent framework
"""

from importlib.metadata import PackageNotFoundError, version as pkg_version
import sys


def _resolve_version() -> str:
    try:
        return pkg_version("nanobot-ai")
    except PackageNotFoundError:
        return "0.1.3.post6"


def _supports_logo(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except UnicodeEncodeError:
        return False


__version__ = _resolve_version()
__logo__ = "🐈" if _supports_logo("🐈") else "nanobot"
