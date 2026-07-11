"""Long-lived httpx clients for outbound LLM calls.

Each per-request `async with httpx.AsyncClient(...)` paid for a fresh TLS
handshake (~100–200 ms). A single shared pool keeps the connection warm,
reuses sockets across requests, and enables HTTP/2 multiplexing.

Usage:
    from core.http_clients import get_openrouter_client
    async with get_openrouter_client().stream("POST", url, ...) as r: ...

The clients are created lazily on first access so that import-time errors
in tests (no event loop yet, no httpx[http2] extras, ...) don't block the
backend from starting. They are closed in main.py's shutdown event.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Connection-pool sizing. Defaults are sized for ~30 chats/min on a small
# VPS; bump via env if you scale out.
_MAX_KEEPALIVE = int(os.getenv("HTTPX_MAX_KEEPALIVE", "20"))
_MAX_CONNECTIONS = int(os.getenv("HTTPX_MAX_CONNECTIONS", "100"))
_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
_LIMITS = httpx.Limits(
    max_keepalive_connections=_MAX_KEEPALIVE,
    max_connections=_MAX_CONNECTIONS,
)


def _build_client(base_url: Optional[str] = None) -> httpx.AsyncClient:
    """Create an AsyncClient with HTTP/2 if available, else fall back to 1.1."""
    try:
        return httpx.AsyncClient(
            base_url=base_url or "",
            http2=True,
            timeout=_TIMEOUT,
            limits=_LIMITS,
        )
    except ImportError:
        # h2 extra missing → still benefit from keep-alive + pool
        logger.warning("httpx[http2] not installed — falling back to HTTP/1.1 keep-alive")
        return httpx.AsyncClient(
            base_url=base_url or "",
            timeout=_TIMEOUT,
            limits=_LIMITS,
        )


_openrouter_client: Optional[httpx.AsyncClient] = None
_generic_client: Optional[httpx.AsyncClient] = None


def get_openrouter_client() -> httpx.AsyncClient:
    """Return the long-lived AsyncClient pinned to OpenRouter's base URL."""
    global _openrouter_client
    if _openrouter_client is None or _openrouter_client.is_closed:
        _openrouter_client = _build_client("https://openrouter.ai/api/v1")
    return _openrouter_client


def get_generic_client() -> httpx.AsyncClient:
    """Return a long-lived AsyncClient with no fixed base URL (for ad-hoc
    outbound calls that don't go to OpenRouter)."""
    global _generic_client
    if _generic_client is None or _generic_client.is_closed:
        _generic_client = _build_client(None)
    return _generic_client


async def close_http_clients() -> None:
    """Close all pooled clients on shutdown."""
    global _openrouter_client, _generic_client
    for name, client in (("openrouter", _openrouter_client), ("generic", _generic_client)):
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
                logger.info(f"Closed {name} httpx client")
            except Exception as e:
                logger.warning(f"Error closing {name} httpx client: {e}")
    _openrouter_client = None
    _generic_client = None
