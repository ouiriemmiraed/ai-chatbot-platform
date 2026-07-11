import chromadb
from chromadb.config import Settings
try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
except ImportError:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

from sentence_transformers import SentenceTransformer, CrossEncoder
import os
import uuid
import json
import hashlib
import time
import threading
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


# ── MongoDB lexical fallback (Sprint C) ─────────────────────────────────────
# When ChromaDB is unreachable, queries fall back to a $text search over the
# `rag_chunks` collection that bulk_ingest mirrors into MongoDB. Recall is
# lower than vector search but the bot keeps answering instead of erroring.
_sync_mongo = None


def _get_sync_mongo_db():
    """Return a sync pymongo db handle (rag_service runs from worker
    contexts where motor's async API is awkward)."""
    global _sync_mongo
    if _sync_mongo is None:
        try:
            from pymongo import MongoClient
            url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
            db_name = os.getenv("DATABASE_NAME", "chatbot_db")
            _sync_mongo = MongoClient(url, serverSelectionTimeoutMS=3000)[db_name]
        except Exception as e:
            logger.warning(f"sync mongo unavailable for RAG fallback: {e}")
            return None
    return _sync_mongo

# Model configuration
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
CROSS_ENCODER_MODEL = os.getenv("CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

# E5 family expects "query: ..." / "passage: ..." prefixes for best retrieval.
# Without them, MRR drops by ~10–15 % (intfloat README).
USES_E5_PREFIXES = "e5" in MODEL_NAME.lower()

# Performance tuning
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "200"))
RETRIEVAL_MULTIPLIER = int(os.getenv("RAG_RETRIEVAL_MULTIPLIER", "8"))
# Cross-encoder score threshold. The ms-marco model scores multilingual content
# very negatively (e.g. -7 for French), so use a permissive threshold to avoid
# filtering out all non-English results. The cross-encoder still helps with
# *ranking* even when absolute scores are low.
MIN_RELEVANCE_SCORE = float(os.getenv("RAG_MIN_SCORE", "-100.0"))
CACHE_TTL = int(os.getenv("RAG_CACHE_TTL", "300"))  # 5 minutes default
BATCH_SIZE = int(os.getenv("RAG_BATCH_SIZE", "64"))  # Batch size for embeddings
BULK_EMBED_BATCH = int(os.getenv("RAG_BULK_EMBED_BATCH", "256"))  # Larger batch for bulk crawl ingestion


class RedisCache:
    """Redis-based cache for RAG query results to avoid redundant embedding + search."""
    
    def __init__(self):
        self._redis = None
        self._available = False
    
    def _connect(self):
        """Lazy connect to Redis."""
        if self._redis is not None:
            return
        try:
            import redis
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            self._redis = redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
            self._redis.ping()
            self._available = True
            logger.info("RAG cache connected to Redis")
        except Exception as e:
            logger.warning(f"Redis cache unavailable (RAG will work without cache): {e}")
            self._redis = None
            self._available = False
    
    def _make_key(self, chatbot_id: str, query: str, n_results: int) -> str:
        """Generate a deterministic cache key prefixed by chatbot_id so we can
        invalidate entries for one chatbot without touching others."""
        raw = f"{query.lower().strip()}:{n_results}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"rag_cache:{chatbot_id}:{digest}"
    
    def get(self, chatbot_id: str, query: str, n_results: int) -> Optional[List[Dict]]:
        """Get cached RAG results. Returns None on miss."""
        self._connect()
        if not self._available:
            return None
        try:
            key = self._make_key(chatbot_id, query, n_results)
            cached = self._redis.get(key)
            if cached:
                logger.debug(f"RAG cache HIT for chatbot {chatbot_id}")
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Redis cache read error: {e}")
        return None
    
    def set(self, chatbot_id: str, query: str, n_results: int, results: List[Dict]):
        """Cache RAG results with TTL. Never cache empty results to avoid
        poisoning the cache when models are still loading."""
        if not results:
            return
        self._connect()
        if not self._available:
            return
        try:
            key = self._make_key(chatbot_id, query, n_results)
            self._redis.setex(key, CACHE_TTL, json.dumps(results, default=str))
            logger.debug(f"RAG cache SET for chatbot {chatbot_id}")
        except Exception as e:
            logger.warning(f"Redis cache write error: {e}")
    
    def invalidate(self, chatbot_id: str):
        """Invalidate cache entries for ONE chatbot only (call after
        ingestion/re-scrape). Pattern is scoped to the chatbot's prefix so
        we never wipe the cache of unrelated chatbots."""
        self._connect()
        if not self._available:
            return
        try:
            pattern = f"rag_cache:{chatbot_id}:*"
            cursor = 0
            wiped = 0
            while True:
                cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                if keys:
                    self._redis.delete(*keys)
                    wiped += len(keys)
                if cursor == 0:
                    break
            logger.info(f"RAG cache invalidated for chatbot {chatbot_id} ({wiped} keys)")
        except Exception as e:
            logger.warning(f"Redis cache invalidation error: {e}")


class RAGService:
    def __init__(self, persist_directory="./chroma_db"):
        self.persist_directory = persist_directory
        self._embedding_model = None
        self._cross_encoder = None
        self._client = None
        self._cache = RedisCache()
        self._model_load_time = 0
        self._model_lock = threading.Lock()
        self._cross_encoder_lock = threading.Lock()
        
        # Pre-create the text splitter (reusable, no need to recreate per call)
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
            separators=["\n\n", "\n", ". ", ", ", " ", ""],  # Better semantic splitting
        )
    
    @property
    def client(self):
        """Lazy-load ChromaDB client."""
        if self._client is None:
            chroma_url = os.getenv("CHROMA_DB_URL")
            chroma_token = os.getenv("CHROMA_AUTH_TOKEN")
            if chroma_url:
                host, port = chroma_url.replace("http://", "").split(":")
                logger.info(f"Connecting to ChromaDB at {host}:{port}")
                settings = Settings(
                    chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
                    chroma_client_auth_credentials=chroma_token
                ) if chroma_token else Settings()
                self._client = chromadb.HttpClient(host=host, port=int(port), settings=settings)
            else:
                logger.info(f"Using local ChromaDB at {self.persist_directory}")
                self._client = chromadb.PersistentClient(path=self.persist_directory)
        return self._client
    
    @property
    def embedding_model(self):
        """Lazy-load embedding model with thread-safe double-checked locking.
        
        Without a lock, concurrent asyncio.to_thread() calls all see
        _embedding_model=None simultaneously and each loads the full model,
        causing 3+ copies (~800MB each) in one process → OOM SIGKILL.
        """
        if self._embedding_model is None:
            with self._model_lock:
                if self._embedding_model is None:  # re-check after acquiring lock
                    start = time.time()
                    hf_token = os.getenv("HF_TOKEN")
                    if "gemma" in MODEL_NAME.lower() and hf_token:
                        try:
                            from huggingface_hub import login
                            login(token=hf_token)
                            logger.info("Logged in to Hugging Face Hub")
                        except ImportError:
                            logger.warning("huggingface_hub not installed")
                    
                    self._embedding_model = SentenceTransformer(MODEL_NAME, token=hf_token)
                    self._model_load_time = time.time() - start
                    logger.info(f"Embedding model '{MODEL_NAME}' loaded in {self._model_load_time:.1f}s")
        return self._embedding_model
    
    @property
    def cross_encoder(self):
        """Lazy-load cross-encoder model with thread-safe locking."""
        if self._cross_encoder is None:
            with self._cross_encoder_lock:
                if self._cross_encoder is None:
                    self._cross_encoder = CrossEncoder(CROSS_ENCODER_MODEL)
                    logger.info(f"Cross-encoder '{CROSS_ENCODER_MODEL}' loaded")
        return self._cross_encoder

    def _apply_prefix(self, texts: List[str], is_query: bool) -> List[str]:
        """Apply E5 'query: '/'passage: ' prefixes when the active model needs them."""
        if not USES_E5_PREFIXES:
            return texts
        prefix = "query: " if is_query else "passage: "
        return [prefix + (t or "") for t in texts]

    @staticmethod
    def _detect_language(text: str) -> str:
        """Best-effort language detection. Returns ISO 639-1 code or 'und'.

        Uses langdetect if installed, else a tiny heuristic based on
        Unicode ranges so the call is non-fatal in test environments.
        """
        if not text or not text.strip():
            return "und"
        try:
            from langdetect import detect, DetectorFactory
            DetectorFactory.seed = 0  # deterministic across runs
            return detect(text)
        except Exception:
            # Heuristic fallback: any Arabic letter -> ar, any French diacritic -> fr, else en
            if any('؀' <= ch <= 'ۿ' for ch in text):
                return "ar"
            if any(ch in "àâçéèêëîïôûùüÿœæ" for ch in text.lower()):
                return "fr"
            return "en"

    def _get_embeddings(self, texts: List[str], is_query: bool = False) -> List[List[float]]:
        """Generate embeddings with batching for large inputs.

        is_query: True for a search query, False for documents being indexed.
        E5-style models require different prefixes for each role.
        """
        prepared = self._apply_prefix(texts, is_query=is_query)
        if len(prepared) <= BATCH_SIZE:
            return self.embedding_model.encode(prepared, show_progress_bar=False).tolist()

        all_embeddings = []
        for i in range(0, len(prepared), BATCH_SIZE):
            batch = prepared[i:i + BATCH_SIZE]
            batch_embeddings = self.embedding_model.encode(batch, show_progress_bar=False)
            all_embeddings.extend(batch_embeddings.tolist())
        return all_embeddings

    def _section_aware_chunks(self, text: str) -> List[str]:
        """Split text into chunks while preserving section context.

        Problem: RecursiveCharacterTextSplitter may separate a heading from its
        content, so a chunk about 'Ticket d'entrée élevé...' loses all context
        that it belongs to 'Les inconvénients et points de vigilance'.

        Solution: split the document into heading-delimited sections first, then
        split each section independently and prepend the heading to every chunk.
        This ensures every chunk is self-contained and semantically searchable.
        """
        import re

        # Split on markdown headings (##, ###, etc.)
        heading_pattern = re.compile(r'^(#{1,6}\s+.+)$', re.MULTILINE)
        parts = heading_pattern.split(text)

        sections: List[tuple] = []  # (heading_context, body_text)
        current_heading = ""

        i = 0
        while i < len(parts):
            part = parts[i]
            if heading_pattern.match(part.strip()):
                current_heading = part.strip()
                i += 1
            else:
                body = part.strip()
                if body:
                    sections.append((current_heading, body))
                i += 1

        # If no headings found, fall back to plain splitting
        if not sections or (len(sections) == 1 and not sections[0][0]):
            return self._text_splitter.split_text(text)

        all_chunks: List[str] = []
        for heading, body in sections:
            # Prepend heading to body so every chunk from this section
            # carries the section context.
            section_text = f"{heading}\n\n{body}" if heading else body
            chunks = self._text_splitter.split_text(section_text)
            for chunk in chunks:
                # If the splitter dropped the heading (chunk starts deep in the
                # body), re-inject it so the chunk is self-contained.
                if heading and not chunk.strip().startswith('#'):
                    prefixed = f"{heading}\n\n{chunk}"
                    # Only add prefix if it fits within a reasonable size
                    if len(prefixed) <= CHUNK_SIZE + len(heading) + 20:
                        chunk = prefixed
                all_chunks.append(chunk)

        return all_chunks

    def add_documents(self, chatbot_id: str, text: str, metadata: Dict[str, Any] = None, invalidate_cache: bool = True):
        """
        Ingests text into the chatbot-specific collection.
        If the collection exists, it appends data.
        Set invalidate_cache=False when ingesting many pages in a batch (e.g. during
        a full crawl) and call rag_service.cache_invalidate(chatbot_id) once at the end.
        """
        import time
        start_time = time.time()
        
        logger.info(f"Starting ingestion for chatbot {chatbot_id} ({len(text)} chars)")
        
        collection = self.client.get_or_create_collection(
            name=f"chatbot_{chatbot_id}", 
            metadata={"chatbot_id": chatbot_id}
        )
        logger.info(f"Collection ready (took {time.time() - start_time:.2f}s)")

        # Use section-aware chunking to preserve heading context in every chunk
        chunk_start = time.time()
        chunks = self._section_aware_chunks(text)
        logger.info(f"Chunking complete: {len(chunks)} chunks (took {time.time() - chunk_start:.2f}s)")
        
        if not chunks:
            return 0
        
        # Deduplicate chunks to avoid redundant embeddings
        unique_chunks = list(dict.fromkeys(chunks))
        if len(unique_chunks) < len(chunks):
            logger.info(f"Deduplication: {len(chunks)} -> {len(unique_chunks)} chunks")
            chunks = unique_chunks
        
        # Generate embeddings (this is typically the slowest part)
        embed_start = time.time()
        embeddings = self._get_embeddings(chunks)
        logger.info(f"Embeddings generated (took {time.time() - embed_start:.2f}s)")
        
        # Force garbage collection to free memory after embeddings
        import gc
        gc.collect()
        
        ids = [str(uuid.uuid4()) for _ in chunks]
        
        metadatas = None
        if metadata:
            # Add chunk index to metadata for traceability
            metadatas = [{**metadata, "chunk_index": i} for i, _ in enumerate(chunks)]

        # Batch upsert for large ingestions
        add_start = time.time()
        for i in range(0, len(chunks), BATCH_SIZE):
            batch_end = min(i + BATCH_SIZE, len(chunks))
            collection.add(
                documents=chunks[i:batch_end],
                embeddings=embeddings[i:batch_end],
                metadatas=metadatas[i:batch_end] if metadatas else None,
                ids=ids[i:batch_end]
            )
        logger.info(f"Added to ChromaDB (took {time.time() - add_start:.2f}s)")

        # Invalidate cache — skipped during batch crawl (caller does it once at the end)
        if invalidate_cache:
            self._cache.invalidate(chatbot_id)

        chunk_count = len(chunks)  # save before cleanup

        # Final aggressive memory cleanup
        del embeddings
        del chunks
        del ids
        import gc
        gc.collect()
        
        total_time = time.time() - start_time
        logger.info(f"Ingested {chunk_count} chunks for chatbot {chatbot_id} (total: {total_time:.2f}s)")
        return chunk_count

    def query(self, chatbot_id: str, query_text: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """Retrieve relevant context chunks. Tries:
            1. Redis cache
            2. ChromaDB (vector + cross-encoder rerank — current behaviour)
            3. MongoDB $text search (lexical fallback if ChromaDB fails)
            4. Empty list (caller will answer with current_page + supplemental)
        """
        from core.llm_chain import chromadb_breaker
        from core.circuit_breaker import CircuitState, CircuitBreakerError

        # 0. Cache (unchanged)
        cached = self._cache.get(chatbot_id, query_text, n_results)
        if cached is not None:
            return cached

        # 1. Try ChromaDB unless its breaker is OPEN
        if chromadb_breaker.state == CircuitState.OPEN and not chromadb_breaker._should_attempt_reset():
            logger.info("ChromaDB breaker OPEN — skipping vector search, going straight to mongo fallback")
            return self._query_mongo_fallback(chatbot_id, query_text, n_results)

        try:
            collection = self.client.get_collection(name=f"chatbot_{chatbot_id}")
            chromadb_breaker._on_success()
        except Exception as e:
            logger.warning(f"ChromaDB get_collection failed for {chatbot_id}: {e}")
            chromadb_breaker._on_failure()
            # Collection-not-found vs ChromaDB-down look the same here; the
            # mongo fallback will tell us which one we're really facing.
            return self._query_mongo_fallback(chatbot_id, query_text, n_results)

        # Check collection size to avoid requesting more results than available
        collection_count = collection.count()
        if collection_count == 0:
            return []

        query_embedding = self._get_embeddings([query_text], is_query=True)

        # 1. Retrieval: Fetch more candidates than needed for re-ranking
        initial_k = min(n_results * RETRIEVAL_MULTIPLIER, collection_count)
        
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=initial_k
        )
        
        if not results or not results['documents'] or not results['documents'][0]:
            return []
        
        documents = results['documents'][0]
        metadatas = results['metadatas'][0] if 'metadatas' in results else [{}] * len(documents)
        distances = results['distances'][0] if 'distances' in results else [0.0] * len(documents)

        # 2. Decide ranking strategy.
        # The ms-marco cross-encoder is English-only. On non-English queries
        # it both (a) wastes 50–100 ms loading + scoring and (b) scrambles
        # ranking with deeply negative scores. Skip it entirely when the
        # query is not English; rely on the multilingual embedding distance.
        CROSS_ENCODER_CONFIDENCE = -3.0
        lang = self._detect_language(query_text)
        scores: List[float]
        if lang == "en":
            pairs = [[query_text, doc] for doc in documents]
            scores = [float(s) for s in self.cross_encoder.predict(pairs)]
            best_ce_score = max(scores) if scores else CROSS_ENCODER_CONFIDENCE - 1
            use_cross_encoder = best_ce_score > CROSS_ENCODER_CONFIDENCE
        else:
            # Non-English: bypass cross-encoder, use a placeholder score so
            # the result objects keep the same shape downstream.
            scores = [0.0] * len(documents)
            best_ce_score = float("nan")
            use_cross_encoder = False

        scored_results = []
        for doc, meta, score, distance in zip(documents, metadatas, scores, distances):
            scored_results.append({
                "content": doc,
                "metadata": meta,
                "score": float(score),
                "distance": float(distance)
            })

        if use_cross_encoder:
            scored_results.sort(key=lambda x: x["score"], reverse=True)
            logger.info(f"RAG: cross-encoder ranking (lang=en, best={best_ce_score:.2f})")
        else:
            scored_results.sort(key=lambda x: x["distance"])
            logger.info(f"RAG: distance ranking (lang={lang}, cross-encoder skipped)")
        
        final_results = scored_results[:n_results]
        logger.info(f"RAG query '{query_text[:50]}' -> {len(final_results)} results (from {len(documents)} candidates)")

        # 4. Cache results
        self._cache.set(chatbot_id, query_text, n_results, final_results)

        return final_results

    def _query_mongo_fallback(self, chatbot_id: str, query_text: str, n_results: int) -> List[Dict[str, Any]]:
        """Lexical fallback: $text search over the rag_chunks mirror collection
        that bulk_ingest writes alongside ChromaDB. Returns the same shape as
        the vector path so the caller doesn't need to special-case anything."""
        db = _get_sync_mongo_db()
        if db is None:
            return []
        try:
            cursor = db.rag_chunks.find(
                {"chatbot_id": chatbot_id, "$text": {"$search": query_text}},
                {"score": {"$meta": "textScore"}, "content": 1, "metadata": 1},
            ).sort([("score", {"$meta": "textScore"})]).limit(n_results)
            results = []
            for doc in cursor:
                results.append({
                    "content": doc.get("content", ""),
                    "metadata": doc.get("metadata", {}),
                    "score": float(doc.get("score", 0.0)),
                    "distance": 1.0,  # synthetic — caller sorts by score elsewhere
                })
            if results:
                logger.info(f"RAG: mongo-text fallback returned {len(results)} results for '{query_text[:50]}'")
            return results
        except Exception as e:
            # If $text index is missing or Mongo is down too, give up gracefully.
            logger.warning(f"RAG: mongo $text fallback failed: {e}")
            return []

    def _mirror_chunks_to_mongo(
        self, chatbot_id: str, prepared_docs: List[Dict],
    ) -> int:
        """Write ingested chunks to MongoDB so the lexical fallback has data.
        Tolerant of Mongo being unreachable; the mirror is best-effort."""
        db = _get_sync_mongo_db()
        if db is None or not prepared_docs:
            return 0
        try:
            # Replace any prior chunks for this chatbot so re-ingestion is
            # idempotent.
            db.rag_chunks.delete_many({"chatbot_id": chatbot_id})
            docs_to_insert = [
                {
                    "chatbot_id": chatbot_id,
                    "content": d["text"],
                    "metadata": d.get("metadata", {}),
                }
                for d in prepared_docs
            ]
            if docs_to_insert:
                db.rag_chunks.insert_many(docs_to_insert, ordered=False)
            return len(docs_to_insert)
        except Exception as e:
            logger.warning(f"_mirror_chunks_to_mongo failed: {e}")
            return 0

    def query_expanded(self, chatbot_id: str, query_text: str, n_results: int = 6) -> List[Dict[str, Any]]:
        """
        Multi-query retrieval that improves recall for compound questions.

        Strategy:
        1. Always run the original query.
        2. If the question contains connectors like "et", "and", "&", "ou", "or",
           also run each sub-phrase as an independent query.
        3. Merge all candidate sets, deduplicate by content, keep top n_results
           ordered by best score (or lowest distance for multilingual queries).
        """
        import re

        # Build list of query variants
        queries = [query_text]

        # Split on common connectors between clauses
        split_pattern = r'\s+(?:et|and|&|ou|or|,)\s+'
        parts = re.split(split_pattern, query_text, flags=re.IGNORECASE)
        if len(parts) > 1:
            for part in parts:
                part = part.strip()
                if len(part) >= 5 and part.lower() != query_text.lower():
                    queries.append(part)

        # Also add a noun-phrase version (remove question words / verbs at the start)
        noun_phrase = re.sub(
            r'^(donnez?[- ]moi|donne[- ]moi|donner|montrer|afficher|indiquer|citer|'
            r'quels?\s+sont\s+les?|what\s+are\s+the|quelles?\s+sont\s+les?|'
            r'liste\s+des?|list\s+of|liste[- ]moi)\s+',
            '', query_text, flags=re.IGNORECASE
        ).strip()
        if noun_phrase and noun_phrase.lower() != query_text.lower():
            queries.append(noun_phrase)

        # Run all queries and collect candidates
        seen_content: set = set()
        merged: List[Dict[str, Any]] = []

        for q in queries:
            # Fetch more than needed per sub-query; we'll trim after merge
            try:
                candidates = self.query(chatbot_id, q, n_results=max(n_results, 4))
            except Exception as e:
                logger.warning(f"RAG sub-query failed for '{q[:40]}': {e}")
                continue
            for item in candidates:
                content_key = item["content"][:120]  # first 120 chars as dedup key
                if content_key not in seen_content:
                    seen_content.add(content_key)
                    merged.append(item)

        # ── Direct index injection for navigation-type queries ─────────────────
        # Vector search may miss blog/site index chunks when nav-menu chunks
        # dominate the top-K candidates.  For queries explicitly about blog,
        # articles, or page listings, always include those index chunks.
        NAV_INDEX_KEYWORDS = {
            'blog', 'article', 'actualit', 'publication', 'billet',
            'news', 'post', 'liste', 'list', 'dernier', 'recent'
        }
        if any(kw in query_text.lower() for kw in NAV_INDEX_KEYWORDS):
            try:
                col = self.client.get_collection(f"chatbot_{chatbot_id}")
                for flag in ("is_blog_index", "is_site_index"):
                    idx_data = col.get(
                        where={flag: True},
                        include=["documents", "metadatas"]
                    )
                    for doc, meta in zip(
                        idx_data.get("documents", []),
                        idx_data.get("metadatas", [])
                    ):
                        ck = doc[:120]
                        if ck not in seen_content:
                            seen_content.add(ck)
                            # Inject with a high score so they rank first
                            merged.append({
                                "content": doc,
                                "metadata": meta,
                                "score": 5.0,
                                "distance": 0.0,
                            })
            except Exception as _e:
                logger.warning(f"Index direct fetch failed: {_e}")
        # ─────────────────────────────────────────────────────────────────────────

        if not merged:
            return []

        # Ranking strategy: when query() runs the cross-encoder (English) the
        # results carry meaningful negative scores; when the cross-encoder is
        # bypassed (non-English), all scores are exactly 0.0 and we MUST sort
        # by distance instead. Detect that case explicitly.
        all_zero = all(item["score"] == 0.0 for item in merged)
        best_score = max(item["score"] for item in merged) if merged else -100.0
        if not all_zero and best_score > -3.0:
            merged.sort(key=lambda x: x["score"], reverse=True)
        else:
            merged.sort(key=lambda x: x["distance"])

        result = merged[:n_results]
        logger.info(
            f"RAG expanded query '{query_text[:50]}' used {len(queries)} sub-queries "
            f"-> {len(merged)} unique candidates -> {len(result)} final"
        )
        return result

    def prepare_document(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict]:
        """Chunk text and prepare metadata WITHOUT generating embeddings.
        Used during crawl to accumulate all chunks before a single bulk embed."""
        chunks = self._section_aware_chunks(text)
        if not chunks:
            return []
        unique_chunks = list(dict.fromkeys(chunks))
        result = []
        for i, chunk in enumerate(unique_chunks):
            meta = {**(metadata or {}), "chunk_index": i}
            result.append({"text": chunk, "metadata": meta})
        return result

    def bulk_ingest(self, chatbot_id: str, prepared_docs: List[Dict]) -> int:
        """Embed ALL accumulated chunks in large batches and store in ChromaDB.
        Much faster than per-page embedding because:
        - One model warm-up instead of N
        - Larger batches = better CPU utilization
        - No GIL contention between threads
        """
        if not prepared_docs:
            return 0

        # ── Global deduplication ──────────────────────────────────────────────────
        # Nav menus, headers, and footers appear on every crawled page and create
        # dozens of identical chunks that spam vector search results.  Deduplicate
        # cross-page using the first 200 chars of each chunk as a fingerprint.
        seen_texts: set = set()
        deduped: list = []
        for doc in prepared_docs:
            key = doc["text"][:200]
            if key not in seen_texts:
                seen_texts.add(key)
                deduped.append(doc)
        if len(deduped) < len(prepared_docs):
            logger.info(
                f"Global dedup: {len(prepared_docs)} -> {len(deduped)} chunks "
                f"({len(prepared_docs) - len(deduped)} duplicate nav/footer chunks removed)"
            )
        prepared_docs = deduped
        # ─────────────────────────────────────────────────────────────────────────

        start_time = time.time()
        logger.info(f"Bulk ingestion starting for chatbot {chatbot_id}: {len(prepared_docs)} chunks")

        collection = self.client.get_or_create_collection(
            name=f"chatbot_{chatbot_id}",
            metadata={"chatbot_id": chatbot_id}
        )

        texts = [d["text"] for d in prepared_docs]
        metadatas = [d["metadata"] for d in prepared_docs]
        ids = [str(uuid.uuid4()) for _ in texts]

        # Embed in large batches for efficiency
        embed_start = time.time()
        all_embeddings = []
        prepared_texts = self._apply_prefix(texts, is_query=False)
        for i in range(0, len(prepared_texts), BULK_EMBED_BATCH):
            batch_end = min(i + BULK_EMBED_BATCH, len(prepared_texts))
            batch_emb = self.embedding_model.encode(
                prepared_texts[i:batch_end], show_progress_bar=False
            ).tolist()
            all_embeddings.extend(batch_emb)
            logger.info(f"  Embedded {batch_end}/{len(texts)} chunks ({time.time() - embed_start:.1f}s)")
        logger.info(f"All embeddings generated (took {time.time() - embed_start:.2f}s)")

        # Store in ChromaDB
        store_start = time.time()
        for i in range(0, len(texts), BATCH_SIZE):
            batch_end = min(i + BATCH_SIZE, len(texts))
            collection.add(
                documents=texts[i:batch_end],
                embeddings=all_embeddings[i:batch_end],
                metadatas=metadatas[i:batch_end],
                ids=ids[i:batch_end]
            )
        logger.info(f"Stored in ChromaDB (took {time.time() - store_start:.2f}s)")

        # Mirror chunks to MongoDB so the lexical fallback has data the day
        # ChromaDB falls over. Best-effort; ChromaDB remains the source of truth.
        mirror_start = time.time()
        mirrored = self._mirror_chunks_to_mongo(chatbot_id, prepared_docs)
        if mirrored:
            logger.info(f"Mirrored {mirrored} chunks to mongo (took {time.time() - mirror_start:.2f}s)")

        del all_embeddings
        import gc
        gc.collect()

        total_time = time.time() - start_time
        logger.info(f"Bulk ingested {len(texts)} chunks for chatbot {chatbot_id} (total: {total_time:.2f}s)")
        return len(texts)

    def delete_collection(self, chatbot_id: str):
        """
        Deletes the entire collection for a chatbot (e.g. to re-scrape).
        """
        try:
            self.client.delete_collection(name=f"chatbot_{chatbot_id}")
            self._cache.invalidate(chatbot_id)
            # Also wipe the mongo mirror so the lexical fallback doesn't
            # surface stale chunks after a re-scrape.
            db = _get_sync_mongo_db()
            if db is not None:
                try:
                    db.rag_chunks.delete_many({"chatbot_id": chatbot_id})
                except Exception as e:
                    logger.warning(f"rag_chunks wipe failed for {chatbot_id}: {e}")
            return True
        except ValueError:
            return False

    def clear_non_document_chunks(self, chatbot_id: str) -> bool:
        """Delete every chunk in the collection EXCEPT user-uploaded documents
        (metadata ``type == 'document'``).

        Used before a FULL re-crawl instead of ``delete_collection``: a crawl
        rebuilds the website chunks from scratch, but uploaded documents are not
        part of the crawl, so wiping the whole collection used to silently
        destroy them. This keeps the document chunks and removes only the
        crawl/web chunks. Worker-safe: needs no file access and re-embeds
        nothing.
        """
        try:
            collection = self.client.get_collection(name=f"chatbot_{chatbot_id}")
        except Exception:
            # Collection doesn't exist yet (first crawl) — nothing to clear.
            return False
        try:
            doc_ids = set(collection.get(where={"type": "document"}).get("ids", []))
            all_ids = collection.get().get("ids", [])
            to_delete = [i for i in all_ids if i not in doc_ids]
            # Batch deletes so a huge collection doesn't build one oversized call.
            for i in range(0, len(to_delete), 5000):
                collection.delete(ids=to_delete[i:i + 5000])
            self._cache.invalidate(chatbot_id)
            # Refresh the mongo lexical mirror: it only ever holds crawl chunks
            # (uploaded docs are not mirrored there), so wiping it is safe — the
            # bulk re-ingest that follows the crawl repopulates it.
            db = _get_sync_mongo_db()
            if db is not None:
                try:
                    db.rag_chunks.delete_many({"chatbot_id": chatbot_id})
                except Exception as e:
                    logger.warning(f"rag_chunks wipe failed for {chatbot_id}: {e}")
            logger.info(
                f"Cleared {len(to_delete)} non-document chunks for chatbot "
                f"{chatbot_id} (kept {len(doc_ids)} uploaded-document chunks)"
            )
            return True
        except Exception as e:
            logger.warning(f"clear_non_document_chunks failed for {chatbot_id}: {e}")
            return False

    def delete_by_source(self, chatbot_id: str, source_name: str):
        """
        Remove all chunks whose metadata 'source' matches source_name.
        Used when deleting an uploaded document from the knowledge base.
        """
        try:
            collection = self.client.get_collection(name=f"chatbot_{chatbot_id}")
            collection.delete(where={"source": source_name})
            self._cache.invalidate(chatbot_id)
            logger.info(f"Deleted RAG chunks for source '{source_name}' in chatbot {chatbot_id}")
        except Exception as e:
            logger.warning(f"delete_by_source failed for '{source_name}': {e}")
    
    def get_collection_stats(self, chatbot_id: str) -> Dict[str, Any]:
        """Get stats about a chatbot's knowledge base."""
        try:
            collection = self.client.get_collection(name=f"chatbot_{chatbot_id}")
            return {
                "chatbot_id": chatbot_id,
                "document_count": collection.count(),
                "model": MODEL_NAME,
                "chunk_size": CHUNK_SIZE,
                "chunk_overlap": CHUNK_OVERLAP,
            }
        except Exception:
            return {"chatbot_id": chatbot_id, "document_count": 0}

# Singleton instance (models are lazy-loaded, so startup is fast)
rag_service = RAGService()


def warmup_models():
    """Pre-load embedding model so the first user query doesn't suffer a
    10–30 s cold-start. The cross-encoder is loaded lazily on the first
    English query (it is bypassed for FR/AR queries entirely)."""
    try:
        logger.info("RAG warmup: loading embedding model...")
        _ = rag_service.embedding_model
        logger.info("RAG warmup: embedding ready")
    except Exception as e:
        logger.error(f"RAG warmup failed (will retry on first query): {e}")
