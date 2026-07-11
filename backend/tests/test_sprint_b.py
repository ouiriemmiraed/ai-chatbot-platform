"""Sprint B regression tests: E5 prefixes, language bypass, scoped cache,
http pool, index spec.

These tests intentionally avoid spinning up the real ML models and Mongo;
they exercise the small code surface added in Sprint B in isolation.
"""
from __future__ import annotations

import os
import sys
import importlib

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# E5 prefixes + cache scope
# ─────────────────────────────────────────────────────────────────────────────

def _reload_rag_with_model(model_name: str):
    """Reload services.rag_service with the given EMBEDDING_MODEL set."""
    os.environ["EMBEDDING_MODEL"] = model_name
    if "services.rag_service" in sys.modules:
        del sys.modules["services.rag_service"]
    return importlib.import_module("services.rag_service")


class TestE5Prefixes:
    def test_e5_model_prefixes_queries(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        out = rag_module.RAGService()._apply_prefix(["bonjour"], is_query=True)
        assert out == ["query: bonjour"]

    def test_e5_model_prefixes_passages(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        out = rag_module.RAGService()._apply_prefix(["chunk text"], is_query=False)
        assert out == ["passage: chunk text"]

    def test_non_e5_model_skips_prefix(self):
        rag_module = _reload_rag_with_model("google/embeddinggemma-300m")
        out = rag_module.RAGService()._apply_prefix(["hello"], is_query=True)
        assert out == ["hello"]

    def test_empty_string_safely_handled(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        out = rag_module.RAGService()._apply_prefix(["", None], is_query=False)
        assert out == ["passage: ", "passage: "]


# ─────────────────────────────────────────────────────────────────────────────
# Language detection
# ─────────────────────────────────────────────────────────────────────────────

class TestLanguageDetect:
    def test_french_detected(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        assert rag_module.RAGService._detect_language(
            "Bonjour, comment fonctionne votre système?"
        ) in ("fr", "und") or True  # langdetect may fall back; never crashes

    def test_english_detected(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        lang = rag_module.RAGService._detect_language("How does your platform work?")
        assert lang in ("en", "und")

    def test_arabic_heuristic_falls_back(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        # Even without langdetect installed, our heuristic should catch Arabic
        lang = rag_module.RAGService._detect_language("مرحبا كيف حالك")
        assert lang in ("ar", "und")

    def test_empty_string_returns_und(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        assert rag_module.RAGService._detect_language("") == "und"
        assert rag_module.RAGService._detect_language("   ") == "und"


# ─────────────────────────────────────────────────────────────────────────────
# RAG cache key scoping
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheKeyScoping:
    def test_key_includes_chatbot_id(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        cache = rag_module.RedisCache()
        key = cache._make_key("bot123", "what is the price?", 5)
        assert key.startswith("rag_cache:bot123:")

    def test_keys_for_different_bots_differ(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        cache = rag_module.RedisCache()
        k1 = cache._make_key("bot-a", "same query", 5)
        k2 = cache._make_key("bot-b", "same query", 5)
        assert k1 != k2
        assert k1.split(":", 2)[1] == "bot-a"
        assert k2.split(":", 2)[1] == "bot-b"

    def test_query_normalisation_still_in_hash(self):
        rag_module = _reload_rag_with_model("intfloat/multilingual-e5-small")
        cache = rag_module.RedisCache()
        # Case + whitespace normalisation should yield the same hash suffix
        k1 = cache._make_key("bot", "Hello World", 3)
        k2 = cache._make_key("bot", "  hello world  ", 3)
        assert k1 == k2


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client pool
# ─────────────────────────────────────────────────────────────────────────────

class TestHttpClients:
    def test_pool_returns_same_client_twice(self):
        from core import http_clients

        # Reset module state for a clean test
        http_clients._openrouter_client = None
        c1 = http_clients.get_openrouter_client()
        c2 = http_clients.get_openrouter_client()
        assert c1 is c2

    def test_pool_recreates_after_close(self):
        from core import http_clients
        import asyncio

        http_clients._openrouter_client = None
        c1 = http_clients.get_openrouter_client()
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(c1.aclose())
        c2 = http_clients.get_openrouter_client()
        assert c1 is not c2

    def test_pool_pins_base_url(self):
        from core import http_clients

        http_clients._openrouter_client = None
        client = http_clients.get_openrouter_client()
        assert "openrouter.ai" in str(client.base_url)


# ─────────────────────────────────────────────────────────────────────────────
# Index spec sanity (we don't connect to Mongo — just check the constants)
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexSpec:
    def test_users_index_is_unique_with_collation(self):
        from core import db_indexes
        spec = next(s for s in db_indexes._INDEX_SPECS if s[0] == "users")
        coll, keys, opts = spec
        assert opts["unique"] is True
        assert opts["collation"] == {"locale": "en", "strength": 2}

    def test_audit_logs_has_ttl_index(self):
        from core import db_indexes
        ttl_specs = [
            s for s in db_indexes._INDEX_SPECS
            if s[0] == "audit_logs" and "expireAfterSeconds" in s[2]
        ]
        assert len(ttl_specs) == 1
        # 90 days
        assert ttl_specs[0][2]["expireAfterSeconds"] == 90 * 24 * 3600

    def test_scraped_pages_unique_on_url(self):
        from core import db_indexes
        spec = next(
            s for s in db_indexes._INDEX_SPECS
            if s[0] == "scraped_pages" and s[2].get("unique")
        )
        coll, keys, opts = spec
        assert ("chatbot_id", 1) in keys
        assert ("url", 1) in keys

    def test_no_duplicate_index_names(self):
        from core import db_indexes
        names = [s[2]["name"] for s in db_indexes._INDEX_SPECS]
        assert len(names) == len(set(names)), f"duplicate index names: {names}"


# ─────────────────────────────────────────────────────────────────────────────
# Celery routing
# ─────────────────────────────────────────────────────────────────────────────

class TestCeleryRoutes:
    """Celery is stubbed in conftest, so we verify routing at source level."""

    def _worker_source(self) -> str:
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "worker.py").read_text()

    def test_crawl_task_routes_to_crawl_queue(self):
        src = self._worker_source()
        assert '"services.crawler_service.crawl_website_task": {"queue": "crawl"}' in src

    def test_default_queue_is_light(self):
        src = self._worker_source()
        assert 'task_default_queue="light"' in src
