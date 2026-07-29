"""Phase 7.9 — bot creation wizard. Pin the parser + fallback shape."""


def test_extract_json_array():
    from services.wizard import _extract_json

    out = _extract_json(
        'sure here: [{"key":"a","question":"q","rationale":"r"}] thanks',
        expect_list=True,
    )
    assert out == [{"key": "a", "question": "q", "rationale": "r"}]


def test_extract_json_object():
    from services.wizard import _extract_json

    out = _extract_json(
        '{"system_instructions":"x","display_name":"y","category":"companion","initial_greeting":"hi"}',
        expect_list=False,
    )
    assert out is not None
    assert out["category"] == "companion"


def test_extract_json_returns_none_for_garbage():
    from services.wizard import _extract_json

    assert _extract_json("no json here at all", expect_list=True) is None
    assert _extract_json("", expect_list=False) is None


def test_extract_json_returns_none_for_bad_json():
    from services.wizard import _extract_json

    # Trailing comma — json.loads rejects
    assert _extract_json('[{"k":1,}]', expect_list=True) is None
