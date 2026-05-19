# ---------------------------------------------------------------------------
# test_envelope_wrap.py — Codex PR #97 round-5 ITEM 3 test.
#
# ⭐ Asserts that a raw `HTTPException(status_code=404, detail="some
# string")` flows through `envelope_aware_http_exception_handler` in
# `app/main.py` + comes out the wire as the locked ApiResponse envelope
# (NOT the raw `{"detail": "some string"}` FastAPI default).
#
# WHY THIS TEST EXISTS
# Pre-round-5 the handler's fallback path emitted FastAPI's default
# `{"detail": <str>}` for non-envelope details. A8 + A16 require EVERY
# error to use the envelope. Round-5 ITEM 3 added the locked
# `_STATUS_TO_LOCKED_ERROR_CODE` map + the wrap logic; this test
# guards against regression.
#
# WHY A FRESH FastAPI APP (not the production `app`)?
# The production `app` doesn't have any handler that raises a bare
# `HTTPException(404, "string")` — all of them either return
# envelope dicts (auth dep, placeholder flag dep) or never raise at
# all. Building a tiny test app + registering the same exception
# handler under test exercises the wrap branch in isolation without
# needing to add a test-only endpoint to the production app.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# fastapi — used to build the tiny test app + raise the HTTPException
# from a test endpoint that exercises the round-5 wrap branch.
from fastapi import FastAPI, HTTPException

# fastapi.testclient — wraps the tiny test app in a synchronous test
# client to drive the request through the handler.
from fastapi.testclient import TestClient

# The handler under test — imported as a regular module function (the
# `@app.exception_handler` decorator on main.py both registers it on
# the production app AND leaves it accessible as a module attribute).
from app.main import envelope_aware_http_exception_handler


def _build_test_app() -> FastAPI:
    """Build a minimal FastAPI app wired with the handler under test.

    WHAT: creates a fresh FastAPI app + registers the production
          envelope_aware_http_exception_handler on it + exposes a
          single endpoint that raises HTTPException(404, "some string").
    WHEN: called by each test in this file at fixture-construction
          time so each test has a fresh app + isolated state.
    WHY:  exercises the handler-under-test on a clean app surface so
          there's no interference from the production app's many
          other registered routes / deps.
    """
    test_app = FastAPI()
    test_app.add_exception_handler(HTTPException, envelope_aware_http_exception_handler)

    @test_app.get("/test/raise-404-string-detail")
    def raise_404_with_string():
        # Raises with a STRING detail — pre-round-5 this would emit
        # FastAPI's default `{"detail": "some string"}`; the round-5
        # wrap branch now wraps it in the envelope.
        raise HTTPException(status_code=404, detail="some string")

    @test_app.get("/test/raise-401-string-detail")
    def raise_401_with_string():
        raise HTTPException(status_code=401, detail="another string")

    @test_app.get("/test/raise-unmapped-status")
    def raise_unmapped_status():
        # 418 (I'm a teapot) isn't in `_STATUS_TO_LOCKED_ERROR_CODE`;
        # tests the fallback to `service_unavailable`.
        raise HTTPException(status_code=418, detail="teapot")

    @test_app.get("/test/raise-with-envelope-dict")
    def raise_with_envelope_dict():
        # Pre-existing envelope-dict path; should pass through verbatim.
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "msg": "pre-built envelope",
                "error": "service_unavailable",
                "data": None,
            },
        )

    return test_app


def test_httpexception_404_string_detail_wraps_to_envelope():
    """HTTPException(404, "some string") → 404 + envelope envelope.

    WHAT: hits a test endpoint that raises HTTPException(404, str);
          asserts the response is the locked envelope shape, NOT the
          raw `{"detail": "some string"}` FastAPI default.
    WHEN: any handler that raises a bare HTTPException with a string
          detail goes through this code path.
    WHY:  A8 + A16 — every error response must be envelope-shaped so
          mobile's parser handles them uniformly. Codex PR #97 round-5
          ITEM 3 — the fallback wrap branch.
    """
    client = TestClient(_build_test_app())
    response = client.get("/test/raise-404-string-detail")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "not_found"
    assert body["msg"] == "some string"
    assert body["data"] is None


def test_httpexception_401_string_detail_wraps_with_unauthorized_code():
    """HTTPException(401, str) → envelope with `error="unauthorized"`.

    WHAT: same shape as the 404 test, but exercises the
          `_STATUS_TO_LOCKED_ERROR_CODE[401]` mapping.
    WHEN: any handler raises a bare 401 with a string detail.
    WHY:  mobile pattern-matches on `error` for the auth flow (401 +
          `unauthorized` triggers re-login); the mapping needs to be
          correct.
    """
    client = TestClient(_build_test_app())
    response = client.get("/test/raise-401-string-detail")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "unauthorized"


def test_httpexception_unmapped_status_falls_back_to_service_unavailable():
    """HTTPException(418, str) → envelope with `error="service_unavailable"`.

    WHAT: 418 isn't in the locked status→code map; the handler falls
          back to `service_unavailable` per the round-5 design.
    WHEN: any handler raises an HTTPException with a status code
          outside the locked map (rare; mostly defensive coverage).
    WHY:  ensures the fallback default exists so the envelope contract
          holds even for status codes the map doesn't know about.
    """
    client = TestClient(_build_test_app())
    response = client.get("/test/raise-unmapped-status")
    assert response.status_code == 418
    body = response.json()
    assert body["error"] == "service_unavailable"


def test_httpexception_envelope_dict_detail_passes_through_verbatim():
    """HTTPException(503, <envelope dict>) → envelope dict returned verbatim.

    WHAT: when the handler's detail is ALREADY an envelope-shaped dict
          (the 4 locked keys present), the wrap branch is skipped and
          the dict flows through unchanged. Guards against the round-5
          wrap branch accidentally wrapping pre-built envelopes (which
          would double-wrap + break mobile's parser).
    WHEN: dependencies / handlers that build their own envelope (the
          placeholder flag dep, the auth dep, BLOCKER-4 stubs).
    WHY:  the envelope contract is single-wrap; double-wrap or
          re-wrap would emit `{success: false, msg: ..., error: ...,
          data: <inner envelope>}` which mobile's parser would
          surface as a confusing error.
    """
    client = TestClient(_build_test_app())
    response = client.get("/test/raise-with-envelope-dict")
    assert response.status_code == 503
    body = response.json()
    # Verbatim shape — `msg` is the inner envelope's msg, NOT wrapped
    # again. If wrap-branch fired by mistake, `msg` would be the
    # str(detail-dict) of the entire inner envelope.
    assert body == {
        "success": False,
        "msg": "pre-built envelope",
        "error": "service_unavailable",
        "data": None,
    }


# ===========================================================================
# RELATED FILES:
#   ../../app/main.py                       — envelope_aware_http_exception_handler
#                                             + _STATUS_TO_LOCKED_ERROR_CODE
#   ../../app/api/errors.py                 — error_response + ErrorCode literal
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                           — locked error-codes list + envelope shape
# ===========================================================================
