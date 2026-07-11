"""SSRF guard shared by the crawler, health-checker and scheduler.

The seed URL of a crawl is validated when the crawl is enqueued
(`routers.chatbot.validate_crawl_url`), but the crawler then follows
*discovered* links and the health-checker / scheduler re-fetch *stored* URLs —
none of which were re-validated. A target site can link or redirect to
`http://169.254.169.254/...` or other internal hosts, which would then be
fetched from inside the Docker network. This module provides one
dependency-free check both can call before any outbound fetch.

Fails closed: if a hostname can't be resolved, it is treated as unsafe.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("http", "https")


def is_private_host(hostname: str) -> bool:
    """True if the hostname resolves to a private / loopback / reserved /
    link-local / multicast / unspecified address (or can't be resolved)."""
    if not hostname:
        return True
    try:
        for entry in socket.getaddrinfo(hostname, None):
            ip = ipaddress.ip_address(entry[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_reserved
                    or ip.is_link_local or ip.is_multicast or ip.is_unspecified):
                return True
    except (socket.gaierror, ValueError, UnicodeError):
        # Cannot resolve / malformed → unsafe.
        return True
    return False


def is_safe_public_url(url: str) -> bool:
    """True only for http(s) URLs whose host resolves to a public address."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if not parsed.hostname:
        return False
    return not is_private_host(parsed.hostname)
