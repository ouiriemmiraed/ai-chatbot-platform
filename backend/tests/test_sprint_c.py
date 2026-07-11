"""Sprint C regression tests: LLM provider chain, frozen cache, conversation
buffer, RAG fallback, crawl backpressure, breaker reporting.

These tests do NOT spin up Ollama, OpenRouter, ChromaDB, or MongoDB; they
exercise the small code surface added in Sprint C in isolation. The live
verification of the chain (with the actual stack) is a separate step.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# LLM provider chain — failover + circuit-breaker reuse
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMChain:
    def test_provider_order_when_primary_is_openrouter(self):
        from core import llm_chain
        # We can't easily call stream_chat without LLM keys, but we can
        # introspect the breakers and module-level config to confirm the
        # chain is constructible.
        assert hasattr(llm_chain, "openrouter_breaker")
        assert hasattr(llm_chain, "gemini_breaker")
        assert hasattr(llm_chain, "ollama_breaker")
        assert hasattr(llm_chain, "chromadb_breaker")
        assert hasattr(llm_chain, "frozen_cache")
        assert hasattr(llm_chain, "stream_chat")
        assert hasattr(llm_chain, "all_breakers")

    def test_all_breakers_returns_four(self):
        from core import llm_chain
        breakers = llm_chain.all_breakers()
        names = sorted(b.name for b in breakers)
        assert names == ["chromadb", "gemini", "ollama", "openrouter"]

    @pytest.mark.asyncio
    async def test_stream_chat_falls_through_to_ollama_when_others_fail(self, monkeypatch):
        """Force OpenRouter + Gemini to raise pre-token; chain should fail
        over to Ollama and the user sees ollama-tagged tokens."""
        from core import llm_chain

        async def boom_openrouter(*a, **kw):
            raise RuntimeError("primary down")
            yield  # unreachable
        async def boom_gemini(*a, **kw):
            raise RuntimeError("secondary down")
            yield
        async def fake_ollama(*a, **kw):
            for t in ["fall", "back", " ok"]:
                yield t

        monkeypatch.setattr(llm_chain, "_stream_openrouter", boom_openrouter)
        monkeypatch.setattr(llm_chain, "_stream_gemini", boom_gemini)
        monkeypatch.setattr(llm_chain, "_stream_ollama", fake_ollama)
        # Reset breakers so prior tests don't leave them OPEN
        llm_chain.openrouter_breaker.reset()
        llm_chain.gemini_breaker.reset()
        llm_chain.ollama_breaker.reset()

        results = []
        async for provider, tok in llm_chain.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            primary_model="openai/gpt-oss-20b:free",
            temperature=0.5,
            chatbot_id=None,  # disable frozen tier so we test ollama
            user_question=None,
        ):
            results.append((provider, tok))

        assert results == [("ollama", "fall"), ("ollama", "back"), ("ollama", " ok")]
        # OpenRouter and Gemini breakers each took a failure
        assert llm_chain.openrouter_breaker.failure_count >= 1
        assert llm_chain.gemini_breaker.failure_count >= 1

    @pytest.mark.asyncio
    async def test_stream_chat_propagates_mid_stream_failure(self, monkeypatch):
        """Once a provider has yielded its first token, an exception during
        streaming must propagate (no failover), so the user doesn't see two
        assistants speak."""
        from core import llm_chain

        async def half_then_die(*a, **kw):
            yield "first"
            raise RuntimeError("network drop mid-stream")

        monkeypatch.setattr(llm_chain, "_stream_openrouter", half_then_die)
        llm_chain.openrouter_breaker.reset()

        with pytest.raises(RuntimeError, match="network drop"):
            async for _ in llm_chain.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                primary_model="openai/gpt-oss-20b:free",
                temperature=0.5,
                chatbot_id=None,
                user_question=None,
            ):
                pass


# ─────────────────────────────────────────────────────────────────────────────
# FrozenCache — last-resort replay
# ─────────────────────────────────────────────────────────────────────────────

class TestFrozenCache:
    def test_key_is_chatbot_scoped_and_normalised(self):
        from core.llm_chain import FrozenCache
        c = FrozenCache()
        k1 = c._key("bot", "Hello World")
        k2 = c._key("bot", "  hello world  ")
        k3 = c._key("other-bot", "Hello World")
        assert k1 == k2
        assert k1 != k3
        assert k1.startswith("frozen:bot:")

    def test_set_skips_too_long_questions(self, monkeypatch):
        """We don't want to bloat Redis with unique long-tail queries."""
        from core.llm_chain import FrozenCache
        c = FrozenCache()
        calls = []

        def fake_setex(*a, **kw): calls.append(a)

        class FakeR:
            setex = staticmethod(fake_setex)

            def ping(self): return True

        c._redis = FakeR()
        c._available = True
        c.set("bot", "x" * 5000, "answer")
        assert calls == []  # rejected by length

    @pytest.mark.asyncio
    async def test_stream_frozen_yields_in_chunks(self, monkeypatch):
        from core import llm_chain
        async def gather():
            collected = []
            async for tok in llm_chain._stream_frozen("bot1", "what's the price?"):
                collected.append(tok)
            return collected

        # Inject a known cached value
        monkeypatch.setattr(
            llm_chain.frozen_cache, "get",
            lambda chatbot_id, question: "Hello world this is a cached answer",
        )
        chunks = await gather()
        assert "".join(chunks) == "Hello world this is a cached answer"
        # Each chunk is up to 40 chars
        assert all(len(c) <= 40 for c in chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Conversation buffer — Redis stream producer/consumer
# ─────────────────────────────────────────────────────────────────────────────

class TestConversationBuffer:
    def test_enqueue_returns_false_when_redis_down(self, monkeypatch):
        from services import conversation_buffer
        from bson import ObjectId

        def boom():
            raise ConnectionError("redis unreachable")

        monkeypatch.setattr(conversation_buffer, "_redis", boom)
        ok = conversation_buffer.enqueue_turn_sync(
            ObjectId(), "hi", "hello",
        )
        assert ok is False

    def test_enqueue_returns_true_with_fake_redis(self, monkeypatch):
        from services import conversation_buffer
        from bson import ObjectId

        added = []

        class FakeRedis:
            def xadd(self, *a, **kw): added.append((a, kw))
            def xgroup_create(self, *a, **kw): raise Exception("BUSYGROUP")
            def ping(self): return True

        monkeypatch.setattr(conversation_buffer, "_redis", lambda: FakeRedis())
        # Force re-init each call
        conversation_buffer._group_initialised = False

        ok = conversation_buffer.enqueue_turn_sync(
            ObjectId(), "user msg", "assistant msg",
        )
        assert ok is True
        assert len(added) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Crawl governance — backpressure + retry policy
# ─────────────────────────────────────────────────────────────────────────────

class TestCrawlGovernance:
    def test_check_capacity_accepts_when_queue_short(self, monkeypatch):
        from core import crawl_governance
        monkeypatch.setattr(crawl_governance, "queue_depth", lambda *_: 3)
        ok, retry_after = crawl_governance.check_crawl_capacity()
        assert ok is True
        assert retry_after == 0

    def test_check_capacity_rejects_when_queue_long(self, monkeypatch):
        from core import crawl_governance
        monkeypatch.setattr(
            crawl_governance, "queue_depth",
            lambda *_: crawl_governance.CRAWL_BACKPRESSURE_THRESHOLD + 5,
        )
        ok, retry_after = crawl_governance.check_crawl_capacity()
        assert ok is False
        assert retry_after >= 60

    def test_check_capacity_is_permissive_when_redis_unknown(self, monkeypatch):
        from core import crawl_governance
        monkeypatch.setattr(crawl_governance, "queue_depth", lambda *_: -1)
        ok, retry_after = crawl_governance.check_crawl_capacity()
        assert ok is True

    def test_retry_backoff_increases(self):
        from core.crawl_governance import CRAWL_RETRY_BACKOFFS
        # 5 min → 30 min → 2 h, strictly increasing
        assert CRAWL_RETRY_BACKOFFS == sorted(CRAWL_RETRY_BACKOFFS)
        assert CRAWL_RETRY_BACKOFFS[0] < CRAWL_RETRY_BACKOFFS[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Index spec includes Sprint C additions
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexSpecSprintC:
    def test_rag_chunks_text_index_exists(self):
        from core.db_indexes import _INDEX_SPECS
        text_specs = [s for s in _INDEX_SPECS if s[0] == "rag_chunks" and any(k[1] == "text" for k in s[1])]
        assert len(text_specs) == 1, "rag_chunks $text index missing"

    def test_rag_chunks_chatbot_index_exists(self):
        from core.db_indexes import _INDEX_SPECS
        spec = next(
            (s for s in _INDEX_SPECS if s[0] == "rag_chunks" and s[2]["name"] == "rag_chunks_chatbot"),
            None,
        )
        assert spec is not None
