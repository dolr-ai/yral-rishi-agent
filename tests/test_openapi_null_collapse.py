"""OpenAPI publication transform: collapse anyOf: [T, null] → plain T.

Python types stay honest (Optional[X] = None where the domain is genuinely
nullable) and the published schema drops the null variant — the OpenAPI 3.1
equivalent of the `required` list carrying optionality. Pins the transform
behavior that the swift-openapi-generator consumers depend on
(apple/swift-openapi-generator#817 drops anyOf-null fields ENTIRELY, so a
published null variant silently deletes that field from generated clients).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _render_spec():
    import sys

    sys.path.insert(0, str(REPO / "app"))
    from main import app

    return app.openapi()


def _count_null_anyof(schema_node):
    if isinstance(schema_node, dict):
        total = 0
        any_of = schema_node.get("anyOf")
        if isinstance(any_of, list) and {"type": "null"} in any_of:
            total += 1
        for value in schema_node.values():
            total += _count_null_anyof(value)
        return total
    if isinstance(schema_node, list):
        return sum(_count_null_anyof(value) for value in schema_node)
    return 0


def test_published_spec_has_no_anyof_null_anywhere():
    spec = _render_spec()
    assert _count_null_anyof(spec) == 0, (
        "anyOf-null leaked into the published spec — swift-openapi-generator "
        "(#817) drops such fields entirely from generated clients"
    )


def test_optional_query_param_collapses_to_plain_type_staying_not_required():
    spec = _render_spec()
    params = spec["paths"]["/api/v2/discovery/influencer-feed"]["get"]["parameters"]
    session_id = next(p for p in params if p["name"] == "session_id")
    # Optional[str] = None on the Python side stays Optional — the published
    # schema is a plain string and the param stays NOT required (absence
    # carries optionality on the wire).
    assert session_id["schema"] == {"type": "string", "title": "Session Id"}, (
        "session_id should collapse to a plain string schema, not "
        f"masquerade: {session_id['schema']}"
    )
    assert session_id["required"] is False


def test_genuinely_nullable_response_field_collapses_but_keeps_default_null():
    spec = _render_spec()
    expires_at = spec["components"]["schemas"]["ConsentReadResponse"]["properties"][
        "expires_at"
    ]
    # datetime | None — genuinely tri-state server-side ("never expires").
    # Published as the plain date-time type, not-required (key absent when
    # open-ended); the None default is dropped (not "default": null noise).
    assert expires_at == {
        "type": "string",
        "format": "date-time",
        "title": "Expires At",
    }, f"expires_at collapse wrong: {expires_at}"
    assert "expires_at" not in spec["components"]["schemas"]["ConsentReadResponse"].get(
        "required", []
    )


def test_nullable_ref_property_collapses_to_the_plain_ref():
    spec = _render_spec()
    image = spec["components"]["schemas"]["GenerateRequestBody"]["properties"]["image"]
    assert image == {"$ref": "#/components/schemas/ImagePayload"}, (
        f"image should collapse to the plain $ref: {image}"
    )


def test_multi_variant_anyof_is_untouched():
    """Negative case: a union of several non-null types must NOT collapse —
    only the exact [T, {"type": "null"}] 2-variant shape does. The collapse
    unit is pure, so pin it directly on a synthetic multi-variant union."""
    from main import _collapse_anyof_null

    multi_variant = {
        "anyOf": [{"type": "string"}, {"type": "integer"}],
        "title": "Union",
    }
    result = _collapse_anyof_null(multi_variant)
    assert result == multi_variant, "multi-variant unions must pass through untouched"

    nested_in_property = {
        "properties": {"field": {"anyOf": [{"$ref": "#/x"}, {"type": "string"}]}}
    }
    assert _collapse_anyof_null(nested_in_property) == nested_in_property


def test_openapi_cache_reuses_collapsed_schema():
    """FastAPI's documented caching contract: the second call returns the
    same (already-collapsed) object without regenerating."""
    import sys

    sys.path.insert(0, str(REPO / "app"))
    from main import app

    first = app.openapi()
    second = app.openapi()
    assert first is second
    assert _count_null_anyof(first) == 0