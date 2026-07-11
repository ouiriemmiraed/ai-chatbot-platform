"""Tests for /chat and /chat/stream.

We mock the OpenRouter HTTP layer with respx so no network call is made and
the SSE framing can be verified deterministically.
"""

import json
import pytest
import respx
from httpx import Response
from bson import ObjectId


@pytest.fixture
async def chatbot_doc(mock_db):
    """Insert a minimal chatbot config and return its id (string)."""
    cb = {
        "user_id": "test-user",
        "name": "TestBot",
        "system_prompt": "You sell shoes.",
        "ai_model": "openai/gpt-oss-20b:free",
        "temperature": 0.5,
        "allowed_domains": [],   # widget unrestricted for the test
        "api_key": None,         # no API-key requirement
        "in_chat_email_capture": False,
        "supplemental_info": "",
    }
    res = await mock_db.chatbot_configs.insert_one(cb)
    return str(res.inserted_id)


@pytest.mark.asyncio
@respx.mock
async def test_chat_stream_emits_sse_frames(app_client, chatbot_doc, mock_db):
    """The /chat/stream endpoint must emit meta + token + done frames."""
    # Mock OpenRouter SSE response. Each line must be a `data: {...}` chunk.
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=Response(200, content=sse_body, headers={"content-type": "text/event-stream"})
    )

    with app_client.stream(
        "POST",
        "/chat/stream",
        json={"chatbot_id": chatbot_doc, "message": "Bonjour"},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        chunks = [line for line in r.iter_lines() if line]

    # Decode the data: lines into objects
    events = []
    for line in chunks:
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass

    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert "token" in types
    assert types[-1] == "done"
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert tokens == "Hello world"


@pytest.mark.asyncio
async def test_chat_blocks_unknown_chatbot(app_client, mock_db):
    fake_id = str(ObjectId())  # valid format, but not in DB
    r = app_client.post(
        "/chat/",
        json={"chatbot_id": fake_id, "message": "hi"},
    )
    assert r.status_code == 404


def test_chat_rejects_invalid_object_id(app_client):
    r = app_client.post(
        "/chat/",
        json={"chatbot_id": "not-an-objectid", "message": "hi"},
    )
    assert r.status_code in (400, 422)


def test_chat_rejects_empty_message(app_client, mock_db):
    r = app_client.post(
        "/chat/",
        json={"chatbot_id": str(ObjectId()), "message": "   "},
    )
    assert r.status_code in (400, 422)


def test_chat_rejects_oversize_message(app_client, mock_db):
    r = app_client.post(
        "/chat/",
        json={"chatbot_id": str(ObjectId()), "message": "x" * 6000},
    )
    assert r.status_code in (400, 422)
