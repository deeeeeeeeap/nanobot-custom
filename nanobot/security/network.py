"""Network security utilities for SSRF protection and internal URL detection."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_URL_RE = re.compile(r"https?://[^\s\"'`;|<>]+", re.IGNORECASE)


def _normalize_addr(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_private(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    addr = _normalize_addr(addr)
    return any(addr in network for network in _BLOCKED_NETWORKS)


def validate_url_target(url: str) -> tuple[bool, str]:
    """Validate a URL before fetching it."""
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return False, str(exc)

    if parsed.scheme not in ("http", "https"):
        return False, f"Only http/https allowed, got '{parsed.scheme or 'none'}'"
    if not parsed.netloc:
        return False, "Missing domain"

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname"

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"

    for info in infos:
        try:
            addr = _normalize_addr(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
        if _is_private(addr):
            return False, f"Blocked: {hostname} resolves to private/internal address {addr}"

    return True, ""


def validate_resolved_url(url: str) -> tuple[bool, str]:
    """Validate a URL after fetch or redirect using its resolved host."""
    try:
        parsed = urlparse(url)
    except Exception:
        return True, ""

    hostname = parsed.hostname
    if not hostname:
        return True, ""

    try:
        addr = _normalize_addr(ipaddress.ip_address(hostname))
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            return True, ""
        for info in infos:
            try:
                addr = _normalize_addr(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
            if _is_private(addr):
                return False, f"Redirect target {hostname} resolves to private address {addr}"
    else:
        if _is_private(addr):
            return False, f"Redirect target is a private address: {addr}"

    return True, ""


def contains_internal_url(command: str) -> bool:
    """Return True when a command string contains a URL pointing to a private target."""
    for match in _URL_RE.finditer(command):
        ok, _ = validate_url_target(match.group(0))
        if not ok:
            return True
    return False


__all__ = [
    "contains_internal_url",
    "validate_resolved_url",
    "validate_url_target",
]
