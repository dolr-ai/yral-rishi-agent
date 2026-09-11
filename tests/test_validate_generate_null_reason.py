"""Regression pin for Sentry #602 — the happy path 500'd for four days.

#501 attached a typed response_model to validate-and-generate-metadata and
declared `reason` a plain `str` to keep anyOf-null out of the spec. But the
prompt at character_generator.py tells the model "reason if invalid, null if
valid", so a VALID concept returns an explicit `"reason": null` — and a
pydantic default only fills an ABSENT key, never a present-but-null one.
Every successful generation failed response validation with a 500; only the
rejection path worked.

Driven through the real route so response_model validation actually runs. A
test that asserted on models.py's source text would have stayed green through
the entire outage.
"""

from fastapi.testclient import TestClient


def _valid_concept_payload():
    """Exactly what character_generator returns on the happy path: the LLM's
    JSON, `reason` explicitly null, plus the avatar the route fills in."""
    return {
        "is_valid": True,
        "reason": None,
        "name": "zara",
        "display_name": "Zara",
        "description": "A cheerful travel guide",
        "initial_greeting": "Hi! I'm Zara.",
        "suggested_messages": ["Where should I go?", "Best food?"],
        "personality_traits": {"tone": "warm"},
        "category": "travel",
        "image_prompt": "a smiling travel guide",
        "avatar_url": "https://example.invalid/zara.png",
    }


def _client(monkeypatch, payload, taken_slugs=()):
    from main import app
    from routes import influencers

    async def fake_generate(concept):
        return payload

    async def fake_pool():
        return object()

    async def fake_get_by_name(pool, name):
        return {"name": name} if name in taken_slugs else None

    monkeypatch.setattr(influencers, "get_current_user", lambda request: "user-1")
    monkeypatch.setattr(
        influencers.character_generator, "validate_and_generate_metadata", fake_generate
    )
    monkeypatch.setattr(influencers, "get_pool", fake_pool)
    monkeypatch.setattr(influencers.influencer_repo, "get_by_name", fake_get_by_name)
    return TestClient(app, raise_server_exceptions=False)


def test_valid_concept_with_null_reason_does_not_500(monkeypatch):
    client = _client(monkeypatch, _valid_concept_payload())

    response = client.post(
        "/api/v1/influencers/validate-and-generate-metadata",
        json={"concept": "a cheerful travel guide"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_valid"] is True
    assert body["display_name"] == "Zara"
    # null on the wire is the honest answer for "valid, so no rejection reason".
    assert body["reason"] is None


def test_rejected_concept_still_carries_its_reason(monkeypatch):
    """The path that DID work must keep working — that asymmetry is what made
    the bug survive: bad concepts got a clean answer, good ones got a 500."""
    client = _client(
        monkeypatch,
        {"is_valid": False, "reason": "Content was flagged as inappropriate"},
    )

    response = client.post(
        "/api/v1/influencers/validate-and-generate-metadata",
        json={"concept": "something inappropriate"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_valid"] is False
    assert body["reason"] == "Content was flagged as inappropriate"


def test_reason_stays_a_plain_not_required_string_in_the_published_spec():
    """The fix must not undo what #501 was for. #504 collapses anyOf-null at
    publication, so an honest Optional still publishes as a plain string —
    a generated client sees no change."""
    from main import app

    schema = app.openapi()["components"]["schemas"]["ValidateAndGenerateResponse"]
    assert schema["properties"]["reason"]["type"] == "string"
    assert "anyOf" not in schema["properties"]["reason"]
    assert "reason" not in schema.get("required", [])


def test_taken_slug_is_settled_before_the_app_commits_to_it(monkeypatch):
    """Motorola 2026-09-11: the LLM proposed slug `meera`, /create 409'd — but
    only after the app had already created the SpacetimeDB bot account and
    uploaded the avatar. The user never sees the slug, so "Try Again" could
    never succeed. validate must hand back a slug that is actually free."""
    client = _client(
        monkeypatch, _valid_concept_payload(), taken_slugs={"zara", "zara-2"}
    )

    response = client.post(
        "/api/v1/influencers/validate-and-generate-metadata",
        json={"concept": "a cheerful travel guide"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "zara-3"


def test_free_slug_is_returned_untouched(monkeypatch):
    client = _client(monkeypatch, _valid_concept_payload())

    response = client.post(
        "/api/v1/influencers/validate-and-generate-metadata",
        json={"concept": "a cheerful travel guide"},
    )

    assert response.json()["name"] == "zara"
