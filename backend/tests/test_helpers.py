"""Unit tests for stand-alone helper functions.

These tests have no I/O dependencies — they exercise pure logic.
"""

import pytest


# ── chat router helpers ──────────────────────────────────────────────────────
class TestOriginAllowed:
    def setup_method(self):
        from routers.chat import _is_origin_allowed
        self.fn = _is_origin_allowed

    def test_exact_match(self):
        assert self.fn("https://example.com", "", ["example.com"])

    def test_referer_fallback(self):
        assert self.fn("", "https://example.com/page", ["example.com"])

    def test_wildcard_subdomain(self):
        assert self.fn("https://shop.example.com", "", ["*.example.com"])

    def test_wildcard_matches_apex(self):
        assert self.fn("https://example.com", "", ["*.example.com"])

    def test_evil_lookalike_rejected(self):
        # Critical: substring matching must NOT happen
        assert not self.fn("https://evil-example.com", "", ["example.com"])
        assert not self.fn("https://example.com.evil.org", "", ["example.com"])

    def test_no_origin_no_referer(self):
        assert not self.fn("", "", ["example.com"])

    def test_empty_allowlist(self):
        assert not self.fn("https://example.com", "", [])

    def test_multiple_domains(self):
        domains = ["a.com", "b.com", "*.c.com"]
        assert self.fn("https://b.com", "", domains)
        assert self.fn("https://x.c.com", "", domains)
        assert not self.fn("https://d.com", "", domains)


# ── auth router helpers ──────────────────────────────────────────────────────
class TestPasswordStrength:
    def setup_method(self):
        from fastapi import HTTPException
        from routers.auth import validate_password_strength
        self.fn = validate_password_strength
        self.exc = HTTPException

    def test_valid_password(self):
        # Must not raise
        self.fn("Strong1!Password")

    def test_too_short(self):
        with pytest.raises(self.exc) as e:
            self.fn("Aa1!a")
        assert "8 characters" in str(e.value.detail)

    def test_no_uppercase(self):
        with pytest.raises(self.exc) as e:
            self.fn("strong1!password")
        assert "uppercase" in str(e.value.detail)

    def test_no_lowercase(self):
        with pytest.raises(self.exc):
            self.fn("STRONG1!PASSWORD")

    def test_no_digit(self):
        with pytest.raises(self.exc):
            self.fn("StrongPassword!")

    def test_no_special_char(self):
        with pytest.raises(self.exc):
            self.fn("Strong1Password")


# ── alert service helpers ────────────────────────────────────────────────────
class TestAlertSeverityThreshold:
    def setup_method(self):
        from services.alert_service import AlertService
        self.svc = AlertService()

    def test_complete_rewrite_passes_critical(self):
        assert self.svc._should_send_alert("complete_rewrite", "critical")

    def test_minor_change_blocked_by_critical(self):
        assert not self.svc._should_send_alert("minor", "critical")

    def test_moderate_passes_normal(self):
        assert self.svc._should_send_alert("moderate", "normal")

    def test_trivial_blocked_by_normal(self):
        assert not self.svc._should_send_alert("trivial", "normal")

    def test_unknown_magnitude_uses_default(self):
        # Defaults to score 3 (moderate) → passes "normal" but not "critical"
        assert self.svc._should_send_alert("???", "normal")
        assert not self.svc._should_send_alert("???", "critical")


# ── smart rescrape pattern matching ──────────────────────────────────────────
class TestPatternMatch:
    def setup_method(self):
        from services.smart_rescrape_service import SmartRescrapeEngine
        self.engine = SmartRescrapeEngine()

    def test_simple_path_match(self):
        assert self.engine._matches_pattern(
            "https://example.com/blog/old/article",
            ["/blog/old/*"],
        )

    def test_no_match(self):
        assert not self.engine._matches_pattern(
            "https://example.com/products",
            ["/blog/*"],
        )

    def test_extension_match(self):
        assert self.engine._matches_pattern(
            "https://example.com/file.pdf",
            ["*.pdf"],
        )
