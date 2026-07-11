"""LLM token pricing — converts token counts into USD spend.

Single source of truth for cost dashboards + the /analytics/cost endpoint.
Prices are quoted per 1 million tokens (the unit every provider publishes).

Sources & notes
---------------
- OpenRouter "free tier" models are billed at $0/M but rate-limited; we still
  charge them as $0 because the user's wallet isn't touched.
- Gemini Flash Lite is cheaper input than output, so we track both legs.
- Ollama runs on our own CPU; we put a tiny estimated electricity figure
  ($0.001/M) so a chatbot served entirely from the fallback isn't shown
  as free. Override via env if you have a better measurement.
- "Frozen" responses cost $0 (just a Redis read).
- Unknown models default to `_DEFAULT_PRICE` so we always produce a number.

Update prices when providers change them; the rest of the analytics layer
reads from this module and needs no other changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class _Price:
    input_per_m: float   # USD per 1 M input tokens
    output_per_m: float  # USD per 1 M output tokens


_DEFAULT_PRICE = _Price(input_per_m=0.50, output_per_m=1.50)

# Keys are normalised model strings — see _normalize_model().
_PRICES: Dict[str, _Price] = {
    # OpenRouter / OpenAI
    "openai/gpt-oss-20b:free":      _Price(0.0, 0.0),
    "openai/gpt-oss-120b:free":     _Price(0.0, 0.0),
    "openai/gpt-4o-mini":           _Price(0.15, 0.60),
    "openai/gpt-4o":                _Price(2.50, 10.00),

    # Google Gemini (via SDK) — current paid rates per 1M tokens (≤128k ctx)
    "gemini-2.5-flash-lite":        _Price(0.10, 0.40),
    "gemini-2.5-flash":             _Price(0.30, 2.50),
    "gemini-2.5-pro":               _Price(1.25, 10.00),

    # Anthropic (via OpenRouter or native)
    "anthropic/claude-haiku-4-5":   _Price(0.25, 1.25),
    "anthropic/claude-sonnet-4-6":  _Price(3.00, 15.00),
    "anthropic/claude-opus-4-7":    _Price(15.00, 75.00),

    # Ollama local — estimated electricity cost on CPU
    "ollama:qwen2.5:3b":            _Price(0.001, 0.001),
    "ollama:qwen2.5:7b-instruct":   _Price(0.002, 0.002),
    "ollama:llama3.2:3b":           _Price(0.001, 0.001),

    # Frozen-cache replay — pure Redis read
    "frozen":                       _Price(0.0, 0.0),
}


def _normalize_model(provider: str, model: Optional[str]) -> str:
    """Build the lookup key. Provider is `openrouter|gemini|ollama|frozen`."""
    if provider == "ollama":
        # The exact model is what Ollama pulled, e.g. qwen2.5:3b.
        m = model or os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5:3b")
        return f"ollama:{m}"
    if provider == "frozen":
        return "frozen"
    if not model:
        return "unknown"
    return model.lower()


def estimate_cost_usd(
    provider: str,
    model: Optional[str],
    tokens_in: int,
    tokens_out: int,
) -> float:
    """Return the USD cost for one (input, output) token pair.

    Always returns a non-negative float — never raises, so callers can
    add this in their hot path without try/except.
    """
    key = _normalize_model(provider, model)
    price = _PRICES.get(key, _DEFAULT_PRICE)
    cost = (tokens_in / 1_000_000) * price.input_per_m + (tokens_out / 1_000_000) * price.output_per_m
    return round(max(0.0, cost), 6)


def known_models() -> list[str]:
    """Used by the admin UI to render the cost-per-model breakdown legend."""
    return sorted(_PRICES.keys())
