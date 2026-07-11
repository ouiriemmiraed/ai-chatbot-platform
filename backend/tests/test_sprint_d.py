"""Sprint D regression tests: pricing, classifier validation, judge schema,
gap clustering math, analytics aggregation correctness."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# llm_pricing
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMPricing:
    def test_known_model_returns_nonzero(self):
        from core.llm_pricing import estimate_cost_usd
        cost = estimate_cost_usd("openrouter", "anthropic/claude-sonnet-4-6", 1000, 500)
        assert cost > 0

    def test_free_tier_is_zero(self):
        from core.llm_pricing import estimate_cost_usd
        cost = estimate_cost_usd("openrouter", "openai/gpt-oss-20b:free", 1000, 1000)
        assert cost == 0.0

    def test_unknown_model_falls_back_to_default(self):
        from core.llm_pricing import estimate_cost_usd
        cost = estimate_cost_usd("openrouter", "vendor/unknown-model-xyz", 1000, 500)
        assert cost > 0  # default price kicks in

    def test_ollama_normalised_with_model(self):
        from core.llm_pricing import estimate_cost_usd
        cost = estimate_cost_usd("ollama", "qwen2.5:3b", 1000, 1000)
        assert cost > 0
        assert cost < 0.01  # very cheap

    def test_frozen_costs_zero(self):
        from core.llm_pricing import estimate_cost_usd
        assert estimate_cost_usd("frozen", None, 100, 1000) == 0.0

    def test_zero_tokens_costs_zero(self):
        from core.llm_pricing import estimate_cost_usd
        assert estimate_cost_usd("openrouter", "openai/gpt-4o", 0, 0) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Classifier validation
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifierValidation:
    def test_validate_payload_normalises_known_enums(self):
        from services.conversation_classifier import _validate_payload
        out = _validate_payload({
            "intent": "SALES",
            "sentiment": "Positive",
            "outcome": "resolved",
            "topic_keywords": ["Pricing", "Shipping  "],
            "language": "FR",
            "is_lead_quality": "high",
            "summary": "  Visiteur a acheté.  ",
        })
        assert out["intent"] == "sales"
        assert out["sentiment"] == "positive"
        assert out["outcome"] == "resolved"
        assert out["topic_keywords"] == ["pricing", "shipping"]
        assert out["language"] == "fr"
        assert out["is_lead_quality"] == "high"
        assert out["summary"] == "Visiteur a acheté."

    def test_unknown_enum_is_coerced_to_safe_default(self):
        from services.conversation_classifier import _validate_payload
        out = _validate_payload({"intent": "alien", "sentiment": "weird", "outcome": "fictional"})
        assert out["intent"] == "other"
        assert out["sentiment"] == "neutral"
        assert out["outcome"] == "partial"

    def test_topic_keywords_capped_at_5(self):
        from services.conversation_classifier import _validate_payload
        out = _validate_payload({"topic_keywords": ["a", "b", "c", "d", "e", "f", "g"]})
        assert len(out["topic_keywords"]) == 5

    def test_topic_keywords_string_coerced_to_list(self):
        from services.conversation_classifier import _validate_payload
        out = _validate_payload({"topic_keywords": "single-string"})
        assert out["topic_keywords"] == ["single-string"]

    def test_summary_truncated_to_500_chars(self):
        from services.conversation_classifier import _validate_payload
        out = _validate_payload({"summary": "x" * 1000})
        assert len(out["summary"]) == 500

    def test_prompt_includes_required_fields(self):
        from services.conversation_classifier import _build_prompt
        p = _build_prompt([
            {"role": "user", "content": "Combien coûte le plan Pro?"},
            {"role": "assistant", "content": "29€/mois."},
        ])
        for field in ("intent", "sentiment", "outcome", "topic_keywords", "language", "is_lead_quality", "summary"):
            assert field in p, f"prompt missing {field}"


# ─────────────────────────────────────────────────────────────────────────────
# Judge validation
# ─────────────────────────────────────────────────────────────────────────────

class TestJudgeValidation:
    def test_validate_clamps_floats(self):
        from services.quality_judge import _validate_score
        s = _validate_score({"groundedness": 1.5, "relevance": -0.3, "tone": 0.7,
                             "hallucination_risk": "low"})
        assert s["groundedness"] == 1.0
        assert s["relevance"] == 0.0
        assert s["tone"] == 0.7

    def test_unknown_risk_defaults_to_medium(self):
        from services.quality_judge import _validate_score
        s = _validate_score({"hallucination_risk": "catastrophic"})
        assert s["hallucination_risk"] == "medium"

    def test_aggregate_combines_three_metrics(self):
        from services.quality_judge import _aggregate_score
        score = _aggregate_score({
            "groundedness": 1.0, "relevance": 1.0, "tone": 1.0,
        })
        assert 0.99 <= score <= 1.0

    def test_aggregate_zero_when_all_zero(self):
        from services.quality_judge import _aggregate_score
        assert _aggregate_score({"groundedness": 0, "relevance": 0, "tone": 0}) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Content gap clustering
# ─────────────────────────────────────────────────────────────────────────────

class TestContentGapClustering:
    def test_cluster_ids_for_two_distinct_inputs(self, monkeypatch):
        """We don't actually run e5 embedding here; we monkey-patch
        _embed_questions with a tiny, deterministic stub."""
        import numpy as np
        from services import content_gap_detector

        # Two well-separated clusters: first 6 questions all 'pricing', next 6 all 'returns'
        def fake_embed(qs):
            v_price = np.array([1.0, 0.0, 0.0])
            v_return = np.array([0.0, 1.0, 0.0])
            out = []
            for q in qs:
                if "price" in q or "pricing" in q.lower():
                    out.append(v_price + np.random.normal(0, 0.01, 3))
                else:
                    out.append(v_return + np.random.normal(0, 0.01, 3))
            return np.array(out)

        monkeypatch.setattr(content_gap_detector, "_embed_questions", fake_embed)

        questions = [f"What about pricing #{i}?" for i in range(6)] + \
                    [f"How do returns #{i} work?" for i in range(6)]
        clusters = content_gap_detector._cluster(questions)
        # Should produce 2 clusters of 6 each
        assert len(clusters) == 2
        sizes = sorted([len(c) for c in clusters])
        assert sizes == [6, 6]

    def test_min_cluster_size_constant_is_at_least_3(self):
        from services.content_gap_detector import MIN_CLUSTER_SIZE
        assert MIN_CLUSTER_SIZE >= 3

    def test_similarity_threshold_in_valid_range(self):
        from services.content_gap_detector import SIMILARITY_THRESHOLD
        assert 0.5 <= SIMILARITY_THRESHOLD <= 0.95


# ─────────────────────────────────────────────────────────────────────────────
# Index spec — Sprint D additions
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexSpecSprintD:
    def test_conversation_tags_indexes(self):
        from core.db_indexes import _INDEX_SPECS
        names = [s[2]["name"] for s in _INDEX_SPECS if s[0] == "conversation_tags"]
        assert "conv_tags_unique" in names
        assert "conv_tags_chatbot_time" in names

    def test_quality_scores_unique_per_turn(self):
        from core.db_indexes import _INDEX_SPECS
        unique = next(
            s for s in _INDEX_SPECS
            if s[0] == "quality_scores" and s[2].get("unique")
        )
        keys = unique[1]
        assert ("conversation_id", 1) in keys
        assert ("turn_index", 1) in keys

    def test_llm_costs_has_ttl(self):
        from core.db_indexes import _INDEX_SPECS
        ttl = [s for s in _INDEX_SPECS if s[0] == "llm_costs" and "expireAfterSeconds" in s[2]]
        assert len(ttl) == 1
        assert ttl[0][2]["expireAfterSeconds"] == 365 * 24 * 3600  # 1 year

    def test_content_gaps_chatbot_status_index(self):
        from core.db_indexes import _INDEX_SPECS
        spec = next(
            (s for s in _INDEX_SPECS if s[0] == "content_gaps"),
            None,
        )
        assert spec is not None
