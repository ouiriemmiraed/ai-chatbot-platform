"""Shared pytest fixtures for the backend test suite.

The tests are designed to run **without** requiring MongoDB, Redis, ChromaDB,
or sentence-transformers to be installed. We:
  - mock get_database() with mongomock_motor (in-memory async Mongo)
  - stub heavy ML deps (chromadb, sentence_transformers, ...) at import time
  - patch the RAG service with a deterministic fake
  - patch network calls with respx
"""

import os
import sys
import types
from pathlib import Path
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# ── 1. Environment defaults required by core/config.py and email_service ────
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017")
os.environ.setdefault("SECRET_KEY", "x" * 64)
os.environ.setdefault("CHROMA_AUTH_TOKEN", "test-chroma-token")
os.environ.setdefault("REDIS_PASSWORD", "test-redis-pwd")
os.environ.setdefault("ENV", "development")
os.environ.setdefault("SMTP_SERVER", "smtp.test")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_USERNAME", "test")
os.environ.setdefault("SMTP_PASSWORD", "test")
os.environ.setdefault("SENDER_EMAIL", "test@test.local")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")


# ── 2. Stub heavy ML / external deps so imports succeed without installing ──
def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _stub_dummy_class(*args, **kwargs):
    class _Dummy:
        def __init__(self, *a, **kw): pass
        def __getattr__(self, name): return _Dummy()
        def __call__(self, *a, **kw): return _Dummy()
    return _Dummy()


# chromadb
if "chromadb" not in sys.modules:
    chromadb_stub = _make_stub_module("chromadb",
        HttpClient=lambda *a, **kw: _stub_dummy_class(),
        PersistentClient=lambda *a, **kw: _stub_dummy_class(),
    )
    _make_stub_module("chromadb.config", Settings=lambda *a, **kw: _stub_dummy_class())

# sentence_transformers
if "sentence_transformers" not in sys.modules:
    class _SBERT:
        def __init__(self, *a, **kw): pass
        def encode(self, texts, **kw):
            import numpy as np  # safe — small
            return np.zeros((len(texts) if hasattr(texts, '__len__') else 1, 8))

    class _CE:
        def __init__(self, *a, **kw): pass
        def predict(self, pairs, **kw):
            return [0.0] * len(pairs)

    _make_stub_module("sentence_transformers",
        SentenceTransformer=_SBERT, CrossEncoder=_CE)

# langchain text splitter
if "langchain" not in sys.modules:
    _make_stub_module("langchain")
if "langchain.text_splitter" not in sys.modules:
    class _RCTS:
        def __init__(self, **kw): pass
        def split_text(self, text): return [text] if text else []
    _make_stub_module("langchain.text_splitter", RecursiveCharacterTextSplitter=_RCTS)
if "langchain_text_splitters" not in sys.modules:
    sys.modules["langchain_text_splitters"] = sys.modules["langchain.text_splitter"]

# google.genai
if "google" not in sys.modules:
    _make_stub_module("google")
if "google.genai" not in sys.modules:
    class _GenaiClient:
        def __init__(self, **kw): pass
        @property
        def models(self): return self
        def generate_content_stream(self, **kw): return iter([])
    _make_stub_module("google.genai", Client=_GenaiClient)
    _make_stub_module("google.genai.types",
        GenerateContentConfig=lambda **kw: _stub_dummy_class())

# playwright (used by services/crawler_service.py via routers/chatbot.py)
if "playwright" not in sys.modules:
    _make_stub_module("playwright")
    _make_stub_module("playwright.async_api", async_playwright=lambda: None)

# celery (used by services/crawler_service.py)
if "celery" not in sys.modules:
    def _shared_task(*a, **kw):
        def decorator(fn): return fn
        return decorator
    class _CeleryApp:
        def __init__(self, *a, **kw): pass
        def send_task(self, *a, **kw): pass
        def task(self, *a, **kw):
            def decorator(fn): return fn
            return decorator
    class _SoftTimeLimitExceeded(Exception): pass
    _make_stub_module("celery", shared_task=_shared_task, Celery=_CeleryApp)
    _make_stub_module("celery.exceptions", SoftTimeLimitExceeded=_SoftTimeLimitExceeded)

# redis (cache, but rag_service does lazy connect)
if "redis" not in sys.modules:
    class _Redis:
        def __init__(self, *a, **kw): pass
        def from_url(*a, **kw): return _Redis()
        def ping(self): return True
        def get(self, k): return None
        def setex(self, *a, **kw): pass
        def scan(self, *a, **kw): return (0, [])
        def delete(self, *a, **kw): pass
    _make_stub_module("redis", from_url=_Redis.from_url, Redis=_Redis)


# ── 3. Standard fixtures ────────────────────────────────────────────────────

@pytest.fixture
def mock_db(monkeypatch):
    """In-memory async Mongo replacement; patches ``get_database``."""
    try:
        from mongomock_motor import AsyncMongoMockClient
    except ImportError:
        pytest.skip("mongomock_motor not installed")

    client = AsyncMongoMockClient()
    db = client["test_chatbot_db"]

    from core import database as db_module
    monkeypatch.setattr(db_module, "client", client, raising=False)
    monkeypatch.setattr(db_module, "db", db, raising=False)
    monkeypatch.setattr(db_module, "get_database", lambda: db)
    return db


@pytest.fixture
def stub_rag(monkeypatch):
    """Replace rag_service.query_expanded with a deterministic stub."""
    from services import rag_service as rag_mod

    def _stub_query_expanded(chatbot_id, query, n=10):
        return [{
            "content": "Notre site offre la livraison gratuite dès 50€.",
            "metadata": {"source": "https://example.com/livraison", "title": "Livraison"},
            "score": 1.5, "distance": 0.1,
        }]

    monkeypatch.setattr(rag_mod.rag_service, "query_expanded", _stub_query_expanded)
    return _stub_query_expanded


async def _async_noop(*a, **kw): return None
async def _async_noop_kw(*a, interval=None, **kw): return None


@pytest.fixture
def app_client(mock_db, stub_rag, monkeypatch):
    """TestClient with all external services mocked."""
    monkeypatch.setattr("services.rag_service.warmup_models", lambda: None)
    monkeypatch.setattr("core.scheduler.scheduler.start", _async_noop)
    monkeypatch.setattr("core.scheduler.scheduler.stop", _async_noop)
    monkeypatch.setattr("core.health_check.health_check_system.start", _async_noop_kw)
    monkeypatch.setattr("core.health_check.health_check_system.stop", _async_noop)
    monkeypatch.setattr("core.database.connect_to_mongo", _async_noop)
    monkeypatch.setattr("core.database.close_mongo_connection", _async_noop)

    from fastapi.testclient import TestClient
    import main
    # Disable rate limiting on every router-level Limiter instance
    # (each router constructs its own Limiter — there are 5+ of them).
    main.app.state.limiter.enabled = False
    for mod_name in list(sys.modules):
        mod = sys.modules.get(mod_name)
        if not mod:
            continue
        lim = getattr(mod, "limiter", None)
        if lim is not None and hasattr(lim, "enabled"):
            lim.enabled = False
        # Re-patch get_database on every router that imported it at module load
        if getattr(mod, "get_database", None) is not None and mod_name != "core.database":
            monkeypatch.setattr(mod, "get_database", lambda: mock_db, raising=False)
    return TestClient(main.app)
