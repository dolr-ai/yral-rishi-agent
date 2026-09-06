"""Coach Fix 2 backend — plain-English bot-summary endpoint.

Mix of behavioral (cache freshness, validator) + source-pin (route
wiring, repo helper, LLM-call shape).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_create_influencer_request_has_no_anyof_null():
    """Swift OpenAPI Generator (apple/swift-openapi-generator#817) drops
    anyOf-null properties entirely, so the generated client silently
    loses every Optional[X] = None field. The create request must
    therefore carry plain defaulted fields — this pins that no
    property in the published schema uses anyOf with a null variant."""
    from main import app

    schemas = app.openapi()["components"]["schemas"]
    schema = schemas["CreateInfluencerRequest"]

    for prop in schema["properties"].values():
        if "anyOf" in prop:
            variants = prop["anyOf"]
            assert not any(
                v == {"type": "null"} for v in variants
            ), f"anyOf-null leaks back into the create request: {prop}"

    # The fields the mobile create flow sends must all be present.
    for field in (
        "name",
        "display_name",
        "system_instructions",
        "bot_principal_id",
        "avatar_url",
        "description",
        "category",
        "personality_traits",
        "initial_greeting",
        "suggested_messages",
        "source",
    ):
        assert field in schema["properties"], f"{field} vanished from the schema"


def test_validate_and_generate_response_declares_actual_wire_shape():
    """The response model must declare what the route actually returns —
    `reason` (not rejection_reason), and the persona fields incl.
    category and image_prompt. Attached as response_model in the routes
    change, this turns the endpoint's 200 from schema-less `{}` into a
    typed contract."""
    from main import app

    schemas = app.openapi()["components"]["schemas"]
    schema = schemas["ValidateAndGenerateResponse"]

    for field in (
        "is_valid",
        "reason",
        "name",
        "display_name",
        "description",
        "avatar_url",
        "initial_greeting",
        "suggested_messages",
        "personality_traits",
        "category",
    ):
        assert field in schema["properties"], f"{field} missing from the response schema"

    assert "rejection_reason" not in schema["properties"]


def test_create_influencer_declares_conflict_response():
    """The create route raises 409 on name collisions but never declared
    it — codegen clients route 409 into the undocumented bucket and
    can't surface 'Name taken' to the user."""
    from main import app

    paths = app.openapi()["paths"]
    responses = paths["/api/v1/influencers/create"]["post"]["responses"]
    assert "409" in responses, "create must declare its 409 name-taken response"