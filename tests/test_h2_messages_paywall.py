"""Phase 21αβ.H2 PR 2 — paywall enforcement on POST /messages.

Source-pin tests for the route wiring. Behavioural tests (fastapi
TestClient) aren't possible locally — fastapi + httpx aren't in the
venv — so wire-level smoke runs in CI / prod. The H2 PR 1 unit tests
already pin the billing_client behaviour (cache TTL, fail-open, Sentry
tags); this file just pins that the route consumes it correctly.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTE = REPO / "app" / "routes" / "chat.py"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── module-level wiring ────────────────────────────────────────────────


def test_chat_route_imports_billing_client():
    """The route file must import the shared client — not duplicate the
    httpx call inline. Pin the import so a future refactor that drops
    the shared module is caught."""
    src = ROUTE.read_text()
    assert "billing_client," in src or "billing_client\n" in src


def test_chat_route_imports_json_response():
    """The 402 path returns a `JSONResponse` so we can set both status
    code and the structured `error` body in one shot. Pin the import."""
    src = ROUTE.read_text()
    assert "from fastapi.responses import JSONResponse" in src


# ─── 402 response shape (mobile contract) ───────────────────────────────


def test_no_access_response_shape_matches_mobile_contract():
    """Mobile expects `{ "error": { "code": "no_chat_access", "message": ... } }`
    so it can map `error.code` → paywall CTA and render `error.message`
    inline. Pin the literal shape so a future refactor can't silently
    rename `code` → `error_code` etc."""
    src = ROUTE.read_text()
    assert '"code": "no_chat_access"' in src
    # The wrapping `error` envelope is what mobile parses against
    assert "_NO_ACCESS_RESPONSE" in src
    pos = src.find("_NO_ACCESS_RESPONSE = {")
    block = src[pos : pos + 800]
    assert '"error":' in block
    assert '"code":' in block
    assert '"message":' in block


def test_no_access_response_message_mentions_subscription():
    """Mobile renders `error.message` inline next to the paywall CTA.
    Pin the message contains the word "Subscription" so a copy edit
    catches it here."""
    src = ROUTE.read_text()
    pos = src.find("_NO_ACCESS_RESPONSE")
    block = src[pos : pos + 800]
    assert "Subscription" in block


# ─── _enforce_chat_access helper ───────────────────────────────────────


def test_enforce_helper_exists_and_takes_user_and_bot():
    """Centralised helper so PR 3 (`/messages/stream` + `/images`) can
    reuse it. Pin the signature so the SSE + image routes can call it
    with the same shape."""
    src = ROUTE.read_text()
    assert (
        "async def _enforce_chat_access(user_id: str, influencer_id: str) -> JSONResponse | None:"
        in src
    )


def test_enforce_helper_calls_billing_client():
    """Helper delegates to the shared `check_chat_access`. No bespoke
    httpx call in the route."""
    src = ROUTE.read_text()
    pos = src.find("async def _enforce_chat_access(")
    body = src[pos : pos + 1500]
    assert "await billing_client.check_chat_access(user_id, influencer_id)" in body


def test_enforce_helper_returns_402_when_denied():
    """Denial path: status 402 + `_NO_ACCESS_RESPONSE` envelope. Pin
    both the status code + the envelope so a refactor can't accidentally
    return 200 with the envelope (would leak past mobile's status-code
    check)."""
    src = ROUTE.read_text()
    pos = src.find("async def _enforce_chat_access(")
    body = src[pos : pos + 1500]
    assert "return JSONResponse(status_code=402, content=_NO_ACCESS_RESPONSE)" in body


def test_enforce_helper_returns_none_when_allowed():
    """Allow path: returns None so the caller's `if paywall is not None`
    guard falls through and the LLM-call path continues. Pin the
    return type contract so a refactor to True/False doesn't break
    the existing call-site pattern."""
    src = ROUTE.read_text()
    pos = src.find("async def _enforce_chat_access(")
    body = src[pos : pos + 1500]
    # The helper's only `return None` lives at the end after the
    # has_access==True path
    assert "return None" in body
    assert "if not result.has_access:" in body


def test_enforce_helper_fail_open_documented():
    """Fail-open posture is INTENTIONAL — billing.yral.com being down
    must NOT take down chat. Pin the docstring so a future PR can't
    silently invert the semantics without thinking about it."""
    src = ROUTE.read_text()
    pos = src.find("async def _enforce_chat_access(")
    body = src[pos : pos + 1500]
    assert "fail-open" in body.lower() or "fail_open" in body
    assert "INTENTIONAL" in body or "intentional" in body
    # cost circuit breaker named as the safety net
    assert "circuit breaker" in body.lower() or "cost" in body.lower()


# ─── call-site ordering in send_message ────────────────────────────────


def test_paywall_check_runs_after_dedup():
    """A dup of a previously-allowed message must replay the same
    cached assistant reply without re-checking billing — billing
    already approved this client_message_id the first time. Pin the
    paywall call sits AFTER the dedup early-return."""
    src = ROUTE.read_text()
    pos = src.find("async def send_message(")
    body = src[pos : pos + 6000]
    dedup_pos = body.find("if client_message_id:")
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    assert dedup_pos != -1, "dedup block not found"
    assert paywall_pos != -1, "paywall call site not found in send_message"
    assert dedup_pos < paywall_pos, (
        "paywall check must run AFTER dedup — otherwise a duplicate "
        "POST forces a fresh billing call instead of replaying"
    )


def test_paywall_check_runs_before_audio_transcription():
    """Audio transcription invokes Gemini transcribe — the brief is
    explicit: paywall MUST gate every LLM call. Pin the paywall call
    sits BEFORE the audio block."""
    src = _read("app/routes/chat.py")
    pos = src.find("async def send_message(")
    body = src[pos : pos + 6000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    audio_pos = body.find("# Audio transcription")
    assert paywall_pos != -1
    assert audio_pos != -1
    assert paywall_pos < audio_pos, (
        "paywall check must run BEFORE audio transcription — audio "
        "transcribe is itself an LLM call"
    )


def test_paywall_check_runs_before_user_message_save():
    """The user message DB write happens AFTER the paywall check —
    otherwise a paywalled user pollutes the message history with a
    user-row that never got an assistant reply, breaking the
    conversation lookup pattern for the assistant's next turn."""
    src = _read("app/routes/chat.py")
    pos = src.find("async def send_message(")
    body = src[pos : pos + 6000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    save_pos = body.find("# Save user message")
    assert paywall_pos != -1
    assert save_pos != -1
    assert paywall_pos < save_pos


def test_paywall_uses_user_id_and_influencer_id():
    """user_id comes from JWT (get_current_user), influencer_id comes
    from the conversation row. Pin both — a future refactor that
    swaps user_id → conv["created_by"] or similar would silently
    change who gets paywalled."""
    src = _read("app/routes/chat.py")
    pos = src.find("async def send_message(")
    body = src[pos : pos + 6000]
    assert "paywall = await _enforce_chat_access(user_id, influencer_id)" in body


def test_paywall_dispatch_short_circuits_on_denial():
    """The `if paywall is not None: return paywall` pattern returns
    the 402 JSONResponse directly without falling through to any
    LLM-call path. Pin the early-return."""
    src = _read("app/routes/chat.py")
    pos = src.find("async def send_message(")
    body = src[pos : pos + 6000]
    paywall_pos = body.find("paywall = await _enforce_chat_access(")
    block = body[paywall_pos : paywall_pos + 500]
    assert "if paywall is not None:" in block
    assert "return paywall" in block


# ─── contract anchor ─────────────────────────────────────────────────


def test_route_references_h2_phase():
    """Phase tag anchored in the helper docstring + the comment block
    so a future grep for "H2" finds the wiring."""
    src = _read("app/routes/chat.py")
    assert "21αβ.H2" in src or "Phase 21αβ.H2" in src
