"""Phase 21αβ.H2 PR 3 — paywall enforcement on SSE stream + image routes.

Wires the `_enforce_chat_access` helper from PR 2 into the two
remaining LLM-bound paths:
  - POST /api/v1/chat/conversations/{id}/messages/stream  (Gemini SSE)
  - POST /api/v1/chat/conversations/{id}/images          (Replicate Flux)

Source-pin tests (fastapi/httpx not in local venv). The PR 1
unit tests pin billing_client behaviour; PR 2 tests pin the route
helper; this file pins the two new call sites + their ordering.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTE = REPO / "app" / "routes" / "chat.py"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── SSE stream route ──────────────────────────────────────────────────


def test_stream_route_calls_enforce_helper():
    """SSE route reuses the PR 2 helper — no bespoke billing logic in
    the streaming path. Pin the exact call shape so a refactor that
    inlines the call (and drifts from /messages) is caught."""
    src = ROUTE.read_text()
    pos = src.find("async def send_message_stream(")
    body = src[pos : pos + 8000]
    assert "paywall = await _enforce_chat_access(user_id, influencer_id)" in body


def test_stream_paywall_returns_402_before_event_stream():
    """The 402 MUST surface at the HTTP level — NOT as an SSE
    `event: error` inside the stream. Mobile reads HTTP status to
    decide whether to render the paywall CTA; an SSE error event
    would arrive AFTER mobile already accepted the stream + would
    require extra parser logic.

    Pin the paywall call sits BEFORE the `event_stream` definition
    (which is where the SSE protocol begins)."""
    src = ROUTE.read_text()
    pos = src.find("async def send_message_stream(")
    body = src[pos : pos + 8000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    event_stream_pos = body.find("async def event_stream():")
    assert paywall_pos != -1, "paywall call missing in stream route"
    assert event_stream_pos != -1, "event_stream block not found"
    assert paywall_pos < event_stream_pos


def test_stream_paywall_runs_after_influencer_fetch():
    """The order matches /messages: auth → conv-access → influencer
    fetch → paywall. Without the influencer row we don't have
    influencer_id to send to billing."""
    src = ROUTE.read_text()
    pos = src.find("async def send_message_stream(")
    body = src[pos : pos + 8000]
    inf_fetch_pos = body.find(
        "inf = await influencer_repo.get_by_id(pool, influencer_id)"
    )
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    assert inf_fetch_pos != -1
    assert paywall_pos != -1
    assert inf_fetch_pos < paywall_pos


def test_stream_paywall_runs_before_user_message_save():
    """Same rationale as /messages: don't pollute message history with
    a paywalled user-row that never gets an assistant reply."""
    src = ROUTE.read_text()
    pos = src.find("async def send_message_stream(")
    body = src[pos : pos + 8000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    save_pos = body.find("user_msg = await message_repo.create(")
    assert paywall_pos != -1
    assert save_pos != -1
    assert paywall_pos < save_pos


def test_stream_paywall_short_circuits_on_denial():
    """The 402 JSONResponse return must happen BEFORE we set up the
    StreamingResponse — otherwise mobile gets the SSE handshake then
    a mid-stream error."""
    src = ROUTE.read_text()
    pos = src.find("async def send_message_stream(")
    body = src[pos : pos + 8000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    block = body[paywall_pos : paywall_pos + 500]
    assert "if paywall is not None:" in block
    assert "return paywall" in block


# ─── images route ──────────────────────────────────────────────────────


def test_images_route_calls_enforce_helper():
    """Image gen is the most expensive class of leak — pin the helper
    call so this path can't slip out of the H2 gate."""
    src = ROUTE.read_text()
    pos = src.find("async def generate_conversation_image(")
    body = src[pos : pos + 4000]
    assert "paywall = await _enforce_chat_access(user_id, influencer_id)" in body


def test_images_paywall_runs_before_prompt_generation():
    """Prompt generation calls an LLM (Gemini) to derive the prompt
    from conversation context. Paywall must gate THAT call too — not
    just the Replicate call. Pin the order."""
    src = ROUTE.read_text()
    pos = src.find("async def generate_conversation_image(")
    body = src[pos : pos + 4000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    prompt_pos = body.find('final_prompt = (body.get("prompt") or "").strip()')
    assert paywall_pos != -1
    assert prompt_pos != -1
    assert paywall_pos < prompt_pos


def test_images_paywall_runs_before_replicate_call():
    """Belt-and-braces: paywall must run before any `replicate.*` call
    (the actual paid generation). Pin: paywall call sits BEFORE the
    first replicate.generate_image* invocation."""
    src = ROUTE.read_text()
    pos = src.find("async def generate_conversation_image(")
    body = src[pos : pos + 4000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    replicate_pos = body.find("await replicate.generate_image")
    assert paywall_pos != -1
    assert replicate_pos != -1
    assert paywall_pos < replicate_pos


def test_images_paywall_runs_after_active_check():
    """Bot-discontinued returns 403 before paywall — a deleted bot is
    a harder error than no-access, mobile should see the 403 first.
    Pin the ordering."""
    src = ROUTE.read_text()
    pos = src.find("async def generate_conversation_image(")
    body = src[pos : pos + 4000]
    active_pos = body.find('inf.get("is_active") == "discontinued"')
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    assert active_pos != -1
    assert paywall_pos != -1
    assert active_pos < paywall_pos


def test_images_paywall_short_circuits_on_denial():
    src = ROUTE.read_text()
    pos = src.find("async def generate_conversation_image(")
    body = src[pos : pos + 4000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    block = body[paywall_pos : paywall_pos + 500]
    assert "if paywall is not None:" in block
    assert "return paywall" in block


# ─── helper reuse (no inline drift) ─────────────────────────────────────


def test_no_inline_billing_check_in_stream_or_images():
    """Both new paths route through `_enforce_chat_access` — they MUST
    NOT call `billing_client.check_chat_access` directly. Drift between
    the 3 routes would mean a future tweak (e.g. a Sentry breadcrumb)
    has to be made in 3 places. Pin: `billing_client.check_chat_access`
    appears in the codebase only inside the helper."""
    src = ROUTE.read_text()
    # Two appearances in the route file: import + helper body
    # Anything more = inline drift
    assert src.count("billing_client.check_chat_access") == 1


def test_three_routes_use_the_helper():
    """All 3 LLM-bound routes call `_enforce_chat_access(user_id, influencer_id)`.
    Count occurrences globally so a future 4th LLM route is easier to
    audit."""
    src = ROUTE.read_text()
    assert (
        src.count("paywall = await _enforce_chat_access(user_id, influencer_id)") == 3
    )


# ─── contract anchor ────────────────────────────────────────────────────


def test_both_routes_carry_h2_phase_anchor():
    """Each call-site has a Phase 21αβ.H2 anchor in the surrounding
    comment so grep for `H2` finds all 3 paywall gates."""
    src = ROUTE.read_text()
    # Two new comments (stream + images) PLUS the one from PR 2 = 3 anchors
    assert src.count("Phase 21αβ.H2") >= 3 or src.count("21αβ.H2") >= 3
