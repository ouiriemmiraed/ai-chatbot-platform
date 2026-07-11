"""Non-streaming LLM completion with the SAME provider fallback chain as the
chat path — for background analytics tasks (conversation classifier, quality
judge, content-gap topic suggester).

Why this exists
---------------
Those Celery tasks used to call Gemini directly with no fallback: a Gemini
rate-limit or outage silently paused conversation tagging, quality scoring and
content-gap detection until Gemini recovered. This helper gives them the same
self-healing chain the chat uses:

    Gemini Flash-Lite (primary)
        ↓ failure
    OpenRouter free model (secondary)
        ↓ failure
    Ollama (tertiary, local — works even when the public internet is down)

It reuses the chat path's per-provider CircuitBreakers (imported from
llm_chain) so provider health is SHARED across chat and analytics — if Gemini
is already tripped open by the chat, analytics skip it immediately too.

Unlike the chat path this is NON-streaming (one blob in, one blob out) and
safe to call from a sync Celery task via `complete_sync()`. It deliberately
creates a FRESH httpx client per call instead of reusing the chat's
long-lived pooled client: Celery prefork workers run each task under a new
asyncio loop (via asyncio.run), and a pooled AsyncClient bound to a previous,
now-closed loop would raise "Event loop is closed". Analytics volume is low,
so a fresh client per call is a fine trade for that robustness.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional, Tuple

import httpx

from core.circuit_breaker import CircuitState
from core.llm_chain import (
    GEMINI_API_KEY,
    OPENROUTER_API_KEY,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    OLLAMA_URL,
    gemini_breaker,
    openrouter_breaker,
    ollama_breaker,
)

logger = logging.getLogger(__name__)

_JSON_SYSTEM_NUDGE = "You must respond with valid JSON only — no prose, no markdown fences."


# ── Provider implementations (non-streaming) ─────────────────────────────────
async def _gemini_complete(prompt, system, model, temperature, max_tokens, json_mode) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key not configured")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
        # gemini-2.x supports a dedicated system_instruction slot.
        system_instruction=system if (system and model.startswith("gemini-2")) else None,
    )
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(model=model, contents=contents, config=config),
    )
    return (resp.text or "").strip()


async def _openrouter_complete(prompt, system, model, temperature, max_tokens, json_mode) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter API key not configured")
    messages = []
    # We nudge JSON via a system message rather than response_format because
    # free models vary in response_format support and a 400 there would burn
    # the whole secondary tier; the callers parse JSON defensively anyway.
    sys_parts = [p for p in (system, _JSON_SYSTEM_NUDGE if json_mode else None) if p]
    if sys_parts:
        messages.append({"role": "system", "content": "\n\n".join(sys_parts)})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", timeout=30.0) as client:
        resp = await client.post("/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter returned no choices")
        return ((choices[0].get("message") or {}).get("content") or "").strip()


async def _ollama_complete(prompt, system, model, temperature, max_tokens, json_mode) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    # Small local models are more verbose than the cloud models, so a tight
    # token budget that's fine for Gemini (e.g. 300 for the quality judge)
    # can truncate the local model's JSON before the closing brace, making it
    # unparseable. Give Ollama a JSON-safe floor without inflating cloud cost.
    num_predict = max(max_tokens, 512) if json_mode else max_tokens
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if json_mode:
        # Ollama's native JSON mode is reliable for small local models.
        payload["format"] = "json"
    timeout = httpx.Timeout(connect=5.0, read=OLLAMA_TIMEOUT, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        resp.raise_for_status()
        return ((resp.json().get("message") or {}).get("content") or "").strip()


# ── The chain ────────────────────────────────────────────────────────────────
async def complete(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 512,
    json_mode: bool = True,
    gemini_model: Optional[str] = None,
    openrouter_model: Optional[str] = None,
) -> Tuple[str, str]:
    """Run prompt through Gemini → OpenRouter → Ollama, returning the first
    non-empty result as (provider_name, text). Raises RuntimeError only if
    every provider fails."""
    gemini_model = gemini_model or os.getenv("ANALYTICS_GEMINI_MODEL", "gemini-2.5-flash-lite")
    openrouter_model = openrouter_model or os.getenv(
        "ANALYTICS_OPENROUTER_MODEL", "openai/gpt-oss-20b:free"
    )

    chain = [
        ("gemini", gemini_breaker,
         lambda: _gemini_complete(prompt, system, gemini_model, temperature, max_tokens, json_mode)),
        ("openrouter", openrouter_breaker,
         lambda: _openrouter_complete(prompt, system, openrouter_model, temperature, max_tokens, json_mode)),
        ("ollama", ollama_breaker,
         lambda: _ollama_complete(prompt, system, OLLAMA_MODEL, temperature, max_tokens, json_mode)),
    ]

    last_error: Optional[BaseException] = None
    for name, breaker, fn in chain:
        if breaker.state == CircuitState.OPEN and not breaker._should_attempt_reset():
            logger.info(f"llm_complete: skip {name} (breaker OPEN)")
            continue
        if breaker.state == CircuitState.OPEN:
            breaker.state = CircuitState.HALF_OPEN
            logger.info(f"llm_complete: {name} HALF_OPEN — probing")
        try:
            text = await fn()
            if not text:
                raise RuntimeError(f"{name} returned empty response")
            breaker._on_success()
            return name, text
        except BaseException as e:
            breaker._on_failure()
            last_error = e
            logger.warning(
                f"llm_complete: {name} failed ({type(e).__name__}: {e}); failing over"
            )
            continue

    raise RuntimeError(
        f"All analytics providers exhausted. Last error: "
        f"{type(last_error).__name__}: {last_error}" if last_error else "All providers exhausted"
    )


def complete_sync(prompt: str, **kwargs) -> Tuple[str, str]:
    """Synchronous wrapper for Celery tasks. Safe under prefork workers
    because it owns its own event loop for the duration of the call."""
    return asyncio.run(complete(prompt, **kwargs))
