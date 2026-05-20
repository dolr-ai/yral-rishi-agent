# ---------------------------------------------------------------------------
# canned_responses.py — user-facing replies the safety stack returns when it
# short-circuits run_turn. Three functions, one per safety layer.
#
# ⭐ START HERE: this module exports exactly three callables —
#   prompt_injection_blocked(conversation_id)  — for H5
#   crisis_response(conversation_id)           — for H4
#   nsfw_blocked(conversation_id)              — for A10
#
# Each returns a `MessageResponse`-shaped DICT (NOT the Pydantic model) so a
# `JSONResponse(content=...)` call in middleware can emit it directly
# without an intermediate `.model_dump()`. The shape is byte-identical
# to `app/models/turn.py::MessageResponse` so mobile clients consuming the
# `ApiResponse<MessageResponse>` envelope through Session 3's public-api see
# zero schema delta whether the reply came from a real LLM (Day-5+) or
# the safety stack.
#
# WHY DICTS NOT PYDANTIC MODELS?
# `JSONResponse(content=...)` accepts a JSON-serialisable dict
# directly; constructing a Pydantic `MessageResponse`, then calling
# `.model_dump(mode='json')` to feed `JSONResponse`, would be
# round-trip serialisation for zero gain. Per A2.1 — keep it simple.
# The schema-equivalence guarantee is enforced by the test suite
# (`test_safety_stack.py` asserts every key MessageResponse requires).
#
# WHY `count_toward_paywall=False` FOR ALL THREE?
# Per the Day-3 directive: safety-blocked turns must NOT count against
# the user's 50-msg paywall window per E7. A user who happens to type
# a self-harm phrase shouldn't burn a paywall slot on a crisis-helpline
# auto-reply. Same logic for prompt-injection + NSFW: the user is being
# blocked, not served, so it's not a paid turn.
#
# WHY THE H4 RESPONSE IS AN OBVIOUSLY-PLACEHOLDER STRING?
# Per the Day-3 directive verbatim: "must be obviously a stub, not a
# wrong helpline number". A wrong number is more harmful than a
# placeholder for someone in crisis. Product owns the real copy +
# helpline (Day-3.5 follow-up); until then the bracketed marker tells
# anyone reading the response: "this is the stub, not the final copy".
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from datetime import datetime, timezone
from uuid import uuid4


# ===========================================================================
# Canned text constants (single source of truth — easy to grep for in
# Sentry / Langfuse traces when triaging false-positive blocks)
# ===========================================================================

# H5 / A10 share the same "I can't help with that." wording — short,
# neutral, doesn't hint at why we blocked. Product can refine later.
GENERIC_BLOCKED_CONTENT: str = "I can't help with that."

# H4's placeholder. Bracketed prefix matches the Day-2 stub pattern so
# grep'ing for "[v2 phase-1 day-" pulls up every still-stubbed surface.
CRISIS_PLACEHOLDER_CONTENT: str = (
    "[v2 phase-1 day-3 crisis response — real helpline copy from "
    "product on day-3.5]"
)


# ===========================================================================
# Shared builder — one place that knows the MessageResponse field set so
# all three response builders stay in lockstep with `models/turn.py`.
# ===========================================================================


def _canned_dto(conversation_id: str, content: str) -> dict:
    """Return a MessageResponse-shaped dict for a safety-blocked reply.

    WHAT: assembles the 8 required MessageResponse fields with the safety
          short-circuit defaults (role=assistant, no media, no client
          message id, fresh UUID, ISO8601 UTC `Z` timestamp, paywall
          count flipped OFF).
    WHEN: invoked by the three public callables below.
    WHY:  one builder means a future MessageResponse schema bump only
          changes ONE file in `app/safety/`; tests will catch any
          downstream divergence.
    """
    # ISO8601 UTC `Z` suffix matches what `app/run_turn.py` writes and
    # what chat-ai persists. Kept identical so a future shadow-traffic
    # comparator (Day 7) sees no timestamp-format delta.
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "id": str(uuid4()),
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": content,
        "media_urls": None,
        "client_message_id": None,
        "created_at": now_iso,
        # Safety-blocked turns don't count toward the paywall — see
        # the file-header rationale on E7.
        "count_toward_paywall": False,
    }


# ===========================================================================
# Public callables — one per safety layer
# ===========================================================================


def prompt_injection_blocked(conversation_id: str) -> dict:
    """Canned reply for H5 (prompt-injection defense).

    WHAT: the MessageResponse a user sees when their input matches the
          prompt-injection rule set.
    WHEN: called by `app/middleware/h5_prompt_injection.py` when the
          dispatcher detects a jailbreak / role-override / base64
          blob / known-bad pattern in `user_message`.
    WHY:  centralised so any tone-of-voice copy edit lands here
          (NOT in middleware) — the detector + the response are
          separate concerns per the file-header "split rationale".
    """
    return _canned_dto(conversation_id, GENERIC_BLOCKED_CONTENT)


def crisis_response(conversation_id: str) -> dict:
    """Canned reply for H4 (crisis / mental-health-adjacent input).

    WHAT: the MessageResponse a user sees when their input contains
          self-harm / suicide / crisis-language keywords.
    WHEN: called by `app/middleware/h4_crisis_detection.py` when the
          dispatcher matches a crisis pattern in `user_message`.
    WHY:  the content is intentionally a bracketed-stub string per
          the Day-3 directive — a wrong helpline number is more
          harmful than a placeholder. Product (Day-3.5) replaces this
          with the real copy + locale-aware helpline routing.
    """
    return _canned_dto(conversation_id, CRISIS_PLACEHOLDER_CONTENT)


def nsfw_blocked(conversation_id: str) -> dict:
    """Canned reply for A10 (NSFW output-filter).

    WHAT: the MessageResponse a user sees when the handler's RESPONSE
          content (not the user's input) matches the NSFW rule set.
    WHEN: called by `app/middleware/a10_nsfw_filter.py` after the
          handler returns, when the response payload contains
          flagged content.
    WHY:  output-side filtering catches cases where the upstream LLM
          (Day-5+) drifts into NSFW territory even though the user
          input was clean. Today the rule set is a tiny keyword list;
          Day-5+ swaps it for the real moderation service classifier.
    """
    return _canned_dto(conversation_id, GENERIC_BLOCKED_CONTENT)


# ===========================================================================
# RELATED FILES:
#   __init__.py                — package marker
#   ../models/turn.py          — MessageResponse schema (shape these dicts mirror)
#   ../middleware/h5_prompt_injection.py
#                              — consumes prompt_injection_blocked()
#   ../middleware/h4_crisis_detection.py
#                              — consumes crisis_response()
#   ../middleware/a10_nsfw_filter.py
#                              — consumes nsfw_blocked()
#   ../run_turn.py             — the handler the safety stack short-circuits
#   ../../tests/test_safety_stack.py
#                              — schema-shape + content assertions land here
# ===========================================================================
