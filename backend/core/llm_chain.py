"""LLM provider chain with per-provider circuit breakers and last-resort
frozen cache.

Why this exists
---------------
A single `/chat/stream` call used to depend on a single OpenRouter request;
any 503, rate-limit or timeout was a user-visible failure. Sprint C makes
the chain self-healing:

    OpenRouter (primary)
        ↓ pre-token failure
    Gemini      (secondary)
        ↓ pre-token failure
    Ollama      (tertiary, local — runs even when the public internet is dead)
        ↓ pre-token failure
    FrozenCache (last resort — replays the last known-good answer for that
                 chatbot_id + question if it has been seen recently)

Each tier is wrapped in a CircuitBreaker so a flapping provider stops being
retried for a while. Once the chain has emitted its first token from a
given provider it commits to that provider — failing over mid-stream would
make the user see two assistants speak at once. Mid-stream errors propagate
so the front-end can render a single "Stream error" frame.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional, Tuple

import httpx

from core.circuit_breaker import CircuitBreaker, CircuitState
from core.http_clients import get_openrouter_client

logger = logging.getLogger(__name__)


# ── Per-provider circuit breakers ────────────────────────────────────────────
openrouter_breaker = CircuitBreaker("openrouter", failure_threshold=5, recovery_timeout=60)
gemini_breaker = CircuitBreaker("gemini", failure_threshold=5, recovery_timeout=60)
ollama_breaker = CircuitBreaker("ollama", failure_threshold=3, recovery_timeout=30)
chromadb_breaker = CircuitBreaker("chromadb", failure_threshold=5, recovery_timeout=60)


# ── Provider config ──────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5:3b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120.0"))
# How long Ollama keeps the fallback model loaded after a call (warm = ~10s
# vs cold ~40s). Idle-unloads after this, so no permanent RAM cost.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "15m")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Stream-level timeouts: how long we wait for the FIRST token before deeming
# a provider dead and failing over. Once the first token arrived we commit.
# Per-provider because their cold-start profiles are very different:
#   - cloud LLMs: ~5 s typical, 15 s pessimistic
#   - Ollama on CPU: model load (~10 s if cold) + prompt eval (~10–15 s)
#   - frozen cache: pure Redis read, must be near-instant
FIRST_TOKEN_TIMEOUT = float(os.getenv("LLM_FIRST_TOKEN_TIMEOUT", "15.0"))
FIRST_TOKEN_TIMEOUT_OLLAMA = float(os.getenv("LLM_FIRST_TOKEN_TIMEOUT_OLLAMA", "45.0"))
FIRST_TOKEN_TIMEOUT_FROZEN = float(os.getenv("LLM_FIRST_TOKEN_TIMEOUT_FROZEN", "5.0"))


def _first_token_timeout_for(provider_name: str) -> float:
    if provider_name == "ollama":
        return FIRST_TOKEN_TIMEOUT_OLLAMA
    if provider_name == "frozen":
        return FIRST_TOKEN_TIMEOUT_FROZEN
    return FIRST_TOKEN_TIMEOUT


# ── Frozen cache (last resort) ───────────────────────────────────────────────
class FrozenCache:
    """Stores known-good answers in Redis with a 30-day TTL. Written on
    every successful stream completion; read only when every live provider
    is exhausted. Conservative normalization (case-fold + trim) prevents
    the cache from blowing up with unique-per-user variants."""

    DEFAULT_TTL = int(os.getenv("FROZEN_CACHE_TTL", str(30 * 24 * 3600)))
    MAX_QUESTION_LEN = 500
    KEY_PREFIX = "frozen"

    def __init__(self) -> None:
        self._redis = None
        self._available = False

    def _connect(self) -> None:
        if self._redis is not None:
            return
        try:
            import redis
            url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            self._redis = redis.from_url(url, decode_responses=True, socket_timeout=2)
            self._redis.ping()
            self._available = True
            logger.info("FrozenCache connected to Redis")
        except Exception as e:
            logger.warning(f"FrozenCache: Redis unavailable ({e})")
            self._redis = None
            self._available = False

    def _key(self, chatbot_id: str, question: str) -> str:
        norm = question.strip().lower()[: self.MAX_QUESTION_LEN]
        digest = hashlib.sha256(norm.encode()).hexdigest()
        return f"{self.KEY_PREFIX}:{chatbot_id}:{digest}"

    def get(self, chatbot_id: str, question: str) -> Optional[str]:
        if not chatbot_id or not question:
            return None
        self._connect()
        if not self._available:
            return None
        try:
            return self._redis.get(self._key(chatbot_id, question))
        except Exception as e:
            logger.warning(f"FrozenCache get error: {e}")
            return None

    def set(self, chatbot_id: str, question: str, answer: str) -> None:
        if not chatbot_id or not question or not answer:
            return
        if len(question) > self.MAX_QUESTION_LEN:
            return  # don't cache long-tail unique queries
        self._connect()
        if not self._available:
            return
        try:
            self._redis.setex(self._key(chatbot_id, question), self.DEFAULT_TTL, answer)
        except Exception as e:
            logger.warning(f"FrozenCache set error: {e}")


frozen_cache = FrozenCache()


# ── Provider implementations ─────────────────────────────────────────────────
async def _stream_openrouter(messages: List[Dict], model: str, temperature: float, out_usage: Optional[dict] = None) -> AsyncIterator[str]:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter API key not configured")
    client = get_openrouter_client()
    # OpenRouter mirrors OpenAI: ask for `usage` in the final chunk by
    # passing stream_options.include_usage so we can bill accurately.
    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": 1024, "stream": True,
        "stream_options": {"include_usage": True},
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    async with client.stream("POST", "/chat/completions", headers=headers, json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload_str = line[len("data:"):].strip()
            if payload_str == "[DONE]":
                return
            try:
                obj = json.loads(payload_str)
            except json.JSONDecodeError:
                continue
            # Some chunks carry `usage` (final), others carry `choices`.
            if out_usage is not None and (u := obj.get("usage")):
                out_usage["tokens_in"] = int(u.get("prompt_tokens") or 0)
                out_usage["tokens_out"] = int(u.get("completion_tokens") or 0)
                out_usage["model"] = obj.get("model") or model
            for choice in obj.get("choices", []):
                content = (choice.get("delta") or {}).get("content")
                if content:
                    yield content


async def _stream_gemini(messages: List[Dict], model: str, temperature: float, out_usage: Optional[dict] = None) -> AsyncIterator[str]:
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key not configured")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    system_content = messages[0]["content"] if messages and messages[0]["role"] == "system" else None
    config_gemini = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=1024,
        system_instruction=system_content if model.startswith("gemini-2") else None,
    )
    history = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m.get("content", "")}]}
        for m in messages if m["role"] != "system"
    ]

    loop = asyncio.get_event_loop()
    iterator = await loop.run_in_executor(
        None, lambda: client.models.generate_content_stream(
            model=model, contents=history, config=config_gemini,
        ),
    )

    def _next(it):
        try:
            return next(it)
        except StopIteration:
            return None

    last_chunk = None
    while True:
        chunk = await loop.run_in_executor(None, _next, iterator)
        if chunk is None:
            break
        last_chunk = chunk
        if chunk.text:
            yield chunk.text

    # Gemini exposes prompt/output token counts on usage_metadata of the
    # final chunk (or any chunk; they accumulate). Capture once.
    if out_usage is not None and last_chunk is not None:
        meta = getattr(last_chunk, "usage_metadata", None)
        if meta is not None:
            out_usage["tokens_in"] = int(getattr(meta, "prompt_token_count", 0) or 0)
            out_usage["tokens_out"] = int(getattr(meta, "candidates_token_count", 0) or 0)
            out_usage["model"] = model


async def _stream_ollama(messages: List[Dict], model: str, temperature: float, out_usage: Optional[dict] = None) -> AsyncIterator[str]:
    """Ollama /api/chat returns NDJSON, one frame per token."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        # Keep the model resident after a fallback so consecutive calls during a
        # provider outage answer warm (~10s) instead of paying the cold-load
        # (~40s) each time. No permanent RAM cost: it unloads after idle.
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {"temperature": temperature, "num_predict": 1024},
    }
    timeout = httpx.Timeout(connect=5.0, read=OLLAMA_TIMEOUT, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (frame.get("message") or {}).get("content")
                if content:
                    yield content
                if frame.get("done"):
                    # Ollama exposes prompt_eval_count + eval_count on the
                    # final frame for accurate billing.
                    if out_usage is not None:
                        out_usage["tokens_in"] = int(frame.get("prompt_eval_count") or 0)
                        out_usage["tokens_out"] = int(frame.get("eval_count") or 0)
                        out_usage["model"] = OLLAMA_MODEL
                    return


async def _stream_frozen(chatbot_id: str, question: str, out_usage: Optional[dict] = None) -> AsyncIterator[str]:
    """Replay a previously-cached answer one chunk at a time so the client
    still sees streaming behaviour. We deliberately add tiny delays so the
    user perceives a real answer rather than an instant blob."""
    cached = frozen_cache.get(chatbot_id, question)
    if not cached:
        raise RuntimeError("No frozen response available for this question")
    # Frozen replay has no LLM cost; we still record token counts so the
    # analytics layer can show "% of conversations served from cache".
    if out_usage is not None:
        out_usage["tokens_in"] = 0
        out_usage["tokens_out"] = len(cached) // 4  # approximate
        out_usage["model"] = "frozen"
    chunk_size = 40
    for i in range(0, len(cached), chunk_size):
        yield cached[i: i + chunk_size]
        await asyncio.sleep(0.04)


# ── The chain itself ─────────────────────────────────────────────────────────
@dataclass
class _ProviderSpec:
    name: str
    breaker: CircuitBreaker
    streamer: callable          # async iterator factory
    needs_question: bool = False  # frozen needs (chatbot_id, question)


def _classify_error(exc: BaseException) -> str:
    """Tiny helper to keep failure logs readable."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return f"network_{type(exc).__name__}"
    return type(exc).__name__


async def stream_chat(
    messages: List[Dict],
    primary_model: str,
    temperature: float,
    chatbot_id: Optional[str] = None,
    user_question: Optional[str] = None,
    out_usage: Optional[dict] = None,
) -> AsyncIterator[Tuple[str, str]]:
    """Yield (provider_name, token) pairs.

    The first yielded pair carries the provider that ended up answering;
    every subsequent pair from this run carries the same name. Once a
    provider has emitted its first token we commit to it — mid-stream
    failures bubble up so the caller can emit a single error frame to the
    client rather than mixing two assistants' voices.

    out_usage: optional mutable dict; populated with
      {"provider": str, "model": str, "tokens_in": int, "tokens_out": int}
    so the caller can compute cost / record analytics.
    """
    is_gemini_primary = primary_model.startswith("gemini-")

    # The primary provider depends on the chatbot's configured model.
    primary_spec = (
        _ProviderSpec(
            "gemini",
            gemini_breaker,
            lambda: _stream_gemini(messages, primary_model, temperature, out_usage),
        )
        if is_gemini_primary
        else _ProviderSpec(
            "openrouter",
            openrouter_breaker,
            lambda: _stream_openrouter(messages, primary_model, temperature, out_usage),
        )
    )

    # If the primary IS Gemini, the secondary is OpenRouter (with a free model)
    # so we still get a non-Gemini fallback before reaching local Ollama.
    secondary_spec = (
        _ProviderSpec(
            "openrouter",
            openrouter_breaker,
            lambda: _stream_openrouter(
                messages, "openai/gpt-oss-20b:free", temperature, out_usage,
            ),
        )
        if is_gemini_primary
        else _ProviderSpec(
            "gemini",
            gemini_breaker,
            lambda: _stream_gemini(messages, "gemini-2.5-flash-lite", temperature, out_usage),
        )
    )

    tertiary_spec = _ProviderSpec(
        "ollama",
        ollama_breaker,
        lambda: _stream_ollama(messages, OLLAMA_MODEL, temperature, out_usage),
    )

    frozen_spec = _ProviderSpec(
        "frozen",
        # No real circuit breaker for frozen — it is a Redis read; if Redis
        # is down we just won't have a value and will raise normally.
        CircuitBreaker("frozen", failure_threshold=999, recovery_timeout=1),
        lambda: _stream_frozen(chatbot_id or "", user_question or "", out_usage),
        needs_question=True,
    )

    chain: List[_ProviderSpec] = [primary_spec, secondary_spec, tertiary_spec]
    if chatbot_id and user_question:
        chain.append(frozen_spec)

    last_error: Optional[BaseException] = None
    for spec in chain:
        if spec.breaker.state == CircuitState.OPEN and not spec.breaker._should_attempt_reset():
            logger.info(f"LLMChain: skip {spec.name} (breaker OPEN)")
            continue
        if spec.breaker.state == CircuitState.OPEN:
            spec.breaker.state = CircuitState.HALF_OPEN
            logger.info(f"LLMChain: {spec.name} HALF_OPEN — probing")

        first_token_seen = False
        try:
            it = spec.streamer().__aiter__()

            # Pull the first token under a strict timeout so a hanging
            # provider doesn't block the whole chain.
            try:
                first = await asyncio.wait_for(
                    it.__anext__(),
                    timeout=_first_token_timeout_for(spec.name),
                )
            except StopAsyncIteration:
                # Provider returned an empty stream — treat as failure.
                raise RuntimeError(f"{spec.name} returned no tokens")

            first_token_seen = True
            yield spec.name, first
            async for tok in it:
                yield spec.name, tok

            spec.breaker._on_success()
            if out_usage is not None:
                out_usage.setdefault("provider", spec.name)
            return  # success — chain terminated

        except BaseException as e:
            err_kind = _classify_error(e)
            spec.breaker._on_failure()
            last_error = e

            if first_token_seen:
                logger.warning(
                    f"LLMChain: {spec.name} failed mid-stream ({err_kind}); "
                    f"propagating (no failover after first token)"
                )
                raise

            logger.warning(f"LLMChain: {spec.name} pre-token failure ({err_kind}: {e}); failing over")
            continue

    raise RuntimeError(
        f"All providers exhausted. Last error: "
        f"{type(last_error).__name__}: {last_error}" if last_error else "All providers exhausted"
    )


def all_breakers() -> List[CircuitBreaker]:
    """Return every breaker the chain manages, for /health/breakers."""
    return [openrouter_breaker, gemini_breaker, ollama_breaker, chromadb_breaker]
