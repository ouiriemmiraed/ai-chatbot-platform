"""Tests verifying the split CORS policy.

* Admin endpoints: credentialed CORS, finite origin allowlist
* Widget endpoints: open CORS, no credentials, any origin
"""

import pytest


def test_widget_chat_allows_arbitrary_origin_via_options(app_client):
    """A pre-flight OPTIONS from any third-party site must be accepted."""
    r = app_client.options(
        "/chat/",
        headers={
            "Origin": "https://customer-site.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-API-Key",
        },
    )
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == "https://customer-site.example"
    assert "X-API-Key" in r.headers["Access-Control-Allow-Headers"]


def test_widget_chat_no_credentials_header(app_client):
    """Widget responses MUST NOT set Allow-Credentials: true."""
    r = app_client.options(
        "/chat/stream",
        headers={
            "Origin": "https://random.example.org",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Either absent or explicitly false — never "true"
    assert r.headers.get("Access-Control-Allow-Credentials", "false") != "true"


def test_chatbot_public_widget_open_to_any_origin(app_client, mock_db):
    """The public bootstrap endpoint should not restrict origin."""
    r = app_client.options(
        "/chatbot/public/507f1f77bcf86cd799439011",
        headers={
            "Origin": "https://random-customer.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("Access-Control-Allow-Origin") in (
        "https://random-customer.example", "*",
    )


def test_admin_endpoint_rejects_unknown_origin(app_client):
    """Admin endpoints must keep the allowlist + credentials policy."""
    # /auth/csrf-token is a credentialed endpoint
    r = app_client.options(
        "/auth/csrf-token",
        headers={
            "Origin": "https://hacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Starlette CORSMiddleware rejects unknown origins by *not* echoing them.
    aco = r.headers.get("Access-Control-Allow-Origin", "")
    assert aco != "https://hacker.example"
