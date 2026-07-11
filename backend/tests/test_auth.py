"""Integration tests for the /auth router.

This platform is invite-only: there is NO public /auth/register endpoint —
superusers create managed users via POST /auth/users. These tests therefore
seed a user directly into the in-memory DB and exercise the real login,
session (/auth/me) and password-reset surface.

Uses an in-memory MongoDB (mongomock_motor) and a TestClient from FastAPI.
External e-mail sending is patched out — we only verify the API contract.
"""

import asyncio
import pytest

from core.auth import get_password_hash


@pytest.fixture(autouse=True)
def patch_email(monkeypatch):
    """Stub all SMTP sends so tests don't try to reach Gmail."""
    monkeypatch.setattr("services.email_service.send_password_reset_email", lambda *a, **kw: None)
    monkeypatch.setattr("services.email_service.send_two_factor_enabled_email", lambda *a, **kw: None)


def _seed_user(db, email, password, **extra):
    """Insert a user straight into the in-memory DB (no register endpoint)."""
    doc = {
        "email": email,
        "username": email.split("@")[0],
        "hashed_password": get_password_hash(password),
        "is_superuser": False,
        "can_create_chatbot": True,
        "two_factor_enabled": False,
    }
    doc.update(extra)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(db.users.insert_one(doc))


def test_login_succeeds_with_valid_credentials(app_client, mock_db):
    _seed_user(mock_db, "eve@example.com", "Strong1!Password")
    r = app_client.post("/auth/login-json", json={
        "email": "eve@example.com",
        "password": "Strong1!Password",
    })
    assert r.status_code == 200, r.text
    assert r.json()["token_type"] == "bearer"
    assert "access_token" in r.json()


def test_login_wrong_password_returns_401(app_client, mock_db):
    _seed_user(mock_db, "frank@example.com", "Strong1!Password")
    r = app_client.post("/auth/login-json", json={
        "email": "frank@example.com",
        "password": "WrongPassword1!",
    })
    assert r.status_code == 401


def test_login_unknown_email_returns_401(app_client, mock_db):
    r = app_client.post("/auth/login-json", json={
        "email": "nobody@example.com",
        "password": "Whatever1!X",
    })
    assert r.status_code == 401


def test_me_returns_profile_without_secrets(app_client, mock_db):
    """/auth/me must never leak the password hash, 2FA secret or reset token."""
    _seed_user(mock_db, "grace@example.com", "Strong1!Password",
               two_factor_secret="TOPSECRET", backup_codes=["a", "b"],
               reset_password_token="resettok")
    login = app_client.post("/auth/login-json", json={
        "email": "grace@example.com",
        "password": "Strong1!Password",
    })
    assert login.status_code == 200, login.text
    r = app_client.get("/auth/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "grace@example.com"
    for secret in ("hashed_password", "two_factor_secret", "backup_codes",
                   "reset_password_token"):
        assert secret not in body, f"/auth/me leaked {secret}"


def test_forgot_password_is_generic(app_client, mock_db):
    """Forgot-password must not reveal whether an account exists."""
    r = app_client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert r.status_code == 200
