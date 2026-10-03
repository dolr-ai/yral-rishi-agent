"""Gemini via AI Studio vs via Vertex, checked on the wire.

Each test drives the real client code and captures the HTTP request it would
send (httpx MockTransport), so what's asserted is the URL and body Google
actually receives. No network.
"""

import asyncio
import importlib
import json

import httpx

import config
from services.llm import embeddings
from services.llm_clients import gemini

VERTEX_BASE = (
    "https://aiplatform.googleapis.com/v1/projects/rishi-atmz"
    "/locations/global/publishers/google"
)
STUDIO_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _capture(monkeypatch, reply: dict) -> list[httpx.Request]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=reply)

    real_client = httpx.AsyncClient

    def client_with_mock_transport(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_with_mock_transport)
    return sent


def _use_backend(monkeypatch, vertex_project: str):
    monkeypatch.setattr(config, "GEMINI_VERTEX_PROJECT", vertex_project)
    monkeypatch.setattr(
        config, "GEMINI_NATIVE_URL", VERTEX_BASE if vertex_project else STUDIO_BASE
    )
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")


CHAT_REPLY = {
    "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
}


def _chat():
    return asyncio.run(
        gemini.complete(
            provider="gemini",
            base_url=None,
            api_key="test-key",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "hi"}],
        )
    )


def test_config_builds_the_vertex_url_from_project_and_location(monkeypatch):
    try:
        monkeypatch.setenv("GEMINI_VERTEX_PROJECT", "rishi-atmz")
        assert importlib.reload(config).GEMINI_NATIVE_URL == VERTEX_BASE
        monkeypatch.setenv("GEMINI_VERTEX_LOCATION", "asia-south1")
        assert importlib.reload(config).GEMINI_NATIVE_URL == (
            "https://asia-south1-aiplatform.googleapis.com/v1/projects/rishi-atmz"
            "/locations/asia-south1/publishers/google"
        )
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT")
        assert importlib.reload(config).GEMINI_NATIVE_URL == STUDIO_BASE
    finally:
        monkeypatch.delenv("GEMINI_VERTEX_PROJECT", raising=False)
        monkeypatch.delenv("GEMINI_VERTEX_LOCATION", raising=False)
        importlib.reload(config)


def test_chat_goes_to_vertex_when_a_project_is_set(monkeypatch):
    _use_backend(monkeypatch, "rishi-atmz")
    sent = _capture(monkeypatch, CHAT_REPLY)
    _chat()
    assert str(sent[0].url).startswith(
        f"{VERTEX_BASE}/models/gemini-2.5-flash:generateContent?"
    )
    assert sent[0].url.params["key"] == "test-key"


def test_chat_stays_on_ai_studio_without_a_project(monkeypatch):
    _use_backend(monkeypatch, "")
    sent = _capture(monkeypatch, CHAT_REPLY)
    _chat()
    assert str(sent[0].url).startswith(
        f"{STUDIO_BASE}/models/gemini-2.5-flash:generateContent?"
    )


def test_embedding_on_vertex_uses_predict_and_reads_its_envelope(monkeypatch):
    _use_backend(monkeypatch, "rishi-atmz")
    values = [0.1] * embeddings.EMBEDDING_DIM
    sent = _capture(monkeypatch, {"predictions": [{"embeddings": {"values": values}}]})

    result = asyncio.run(embeddings.embed_text("identity: name = Rahul"))

    assert result == values
    assert str(sent[0].url).startswith(
        f"{VERTEX_BASE}/models/{embeddings.EMBEDDING_MODEL}:predict?"
    )
    body = json.loads(sent[0].content)
    assert body == {
        "instances": [{"content": "identity: name = Rahul"}],
        "parameters": {"outputDimensionality": 768},
    }


def test_embedding_on_ai_studio_is_unchanged(monkeypatch):
    _use_backend(monkeypatch, "")
    values = [0.2] * embeddings.EMBEDDING_DIM
    sent = _capture(monkeypatch, {"embedding": {"values": values}})

    result = asyncio.run(embeddings.embed_text("x"))

    assert result == values
    assert str(sent[0].url).startswith(
        f"{STUDIO_BASE}/models/{embeddings.EMBEDDING_MODEL}:embedContent?"
    )
    assert json.loads(sent[0].content)["outputDimensionality"] == 768
