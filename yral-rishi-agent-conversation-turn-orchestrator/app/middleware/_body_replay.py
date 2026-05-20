# ---------------------------------------------------------------------------
# _body_replay.py — read the request body once, then replay it for downstream
# middleware + the handler via a custom ASGI `receive` callable.
#
# ⭐ START HERE: ONE function — `read_and_replay_body(request)` — exposed.
# Call it inside a middleware's `dispatch()` to inspect the request
# body without preventing the next layer from re-reading it.
#
# THE PROBLEM THIS SOLVES
# `await request.body()` consumes the underlying ASGI receive stream
# the first time it's called. Modern Starlette caches the bytes on
# the `Request` object via `_body` so the SAME `Request` can re-read
# them. BUT — and this is the gotcha — `BaseHTTPMiddleware.call_next`
# does not pass the same `Request` object to the inner app; it
# forwards `request.receive` (the receive CALLABLE) and the inner
# layer constructs its own `Request` from scope + that receive.
#
# If we don't replay the body, the inner Request's `body()` call
# pulls from the underlying receive — which is now drained — and
# the handler gets an empty body. FastAPI/Pydantic then emits 422
# because `conversation_id` and `user_message` are missing.
#
# THE FIX
# After reading the body via `await request.body()`, patch
# `request._receive` to a closure that synthesises a single
# `http.request` message carrying the cached bytes. Subsequent
# layers calling `request.receive` get the replay closure, NOT the
# drained underlying receive, so their `body()` works.
#
# WHY NOT BUFFER AT THE ASGI LEVEL INSTEAD?
# Pure-ASGI buffering (the `while message.get("more_body"): ...`
# pattern) is more robust but verbose. Each middleware would have
# to do its own buffer + replay since the inner app sees the
# replay-receive, not the cached `_body`. The BaseHTTPMiddleware
# pattern with `_receive` patching needs the helper to fire ONCE
# in the outermost safety layer that reads the body; downstream
# safety layers re-call `await request.body()` and get the cached
# `_body` for free.
#
# WHY A LEADING-UNDERSCORE FILENAME?
# Marks the helper as internal to `app/middleware/`. Other services
# spawned from the same template shouldn't import it — the cross-
# service contract is the middleware behaviour, not the helper.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `Request` — Starlette's typed request wrapper. We monkey-patch its
# private `_receive` callable to replay the cached body bytes to
# downstream middlewares + the handler, so the second `await
# request.body()` call doesn't drain an already-consumed stream.
from starlette.requests import Request


async def read_and_replay_body(request: Request) -> bytes:
    """Read the request body once + patch receive so downstream re-reads succeed.

    WHAT: calls `await request.body()` (which consumes the underlying
          receive stream + caches the bytes on `request._body`), then
          replaces `request._receive` with a closure that re-emits the
          cached bytes as a single `http.request` message. Returns the
          body bytes for the caller to inspect.
    WHEN: called by `h5_prompt_injection.py` (the OUTERMOST safety
          layer that needs the body). Subsequent safety layers can
          rely on `await request.body()` returning the cached `_body`
          for free.
    WHY:  without the replay patch, `BaseHTTPMiddleware.call_next`'s
          inner app constructs a new `Request` from the drained
          receive callable — and the handler would see an empty body
          + emit 422 even on perfectly valid input.

    Returns:
        The raw body bytes. Caller is responsible for `json.loads` /
        validation; this helper does NOT decode or validate.
    """
    # First read drains the underlying receive AND caches bytes on
    # request._body. After this line, request.body() is idempotent.
    body = await request.body()

    # Build an ASGI-compliant ONE-SHOT receive closure.
    #
    # Codex PR-#112 round-11 BLOCKER: the previous implementation
    # returned the same body on EVERY call to `receive()`. That
    # violates ASGI receive-stream semantics — the protocol guarantees
    # the body is delivered ONCE, then the consumer transitions to
    # waiting for `http.disconnect`. A consumer that calls receive
    # twice (e.g. Starlette's stream-iteration loop checking for end-
    # of-body) would see a duplicate body on the second call instead
    # of the expected `more_body=False`-then-`http.disconnect`
    # sequence.
    #
    # The closure below uses a `_body_delivered` flag captured in the
    # closure scope:
    #   first call  → returns `http.request` with the cached body +
    #                 `more_body=False` (the standard end-of-body
    #                 signal in ASGI).
    #   subsequent calls → returns `http.disconnect` (the protocol's
    #                      indicator that the client has gone away;
    #                      consumers should stop reading + finalise
    #                      their response).
    #
    # `more_body=False` on the first call is the load-bearing signal
    # downstream consumers use to know the stream is done. The
    # `http.disconnect` follow-up is the protocol's "no more events"
    # state — without it, a poorly-behaved consumer could loop
    # forever on receive().
    _body_delivered = False

    async def replay_receive() -> dict:
        nonlocal _body_delivered
        if _body_delivered:
            return {"type": "http.disconnect"}
        _body_delivered = True
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    # Patch the underlying receive callable on the Request. Starlette's
    # BaseHTTPMiddleware uses `request.receive` (which delegates to
    # `_receive`) to wire the inner app's receive in `call_next`, so
    # this patch propagates downstream.
    request._receive = replay_receive

    # Stash the body bytes on request.state (scope-shared) so middleware
    # layers AFTER `call_next` returns can still read them. The
    # per-layer Request instances Starlette's BaseHTTPMiddleware builds
    # do NOT share `_body` (each instance caches independently); but
    # `state` lives on `scope["state"]` so all layers see the same
    # value. Codex PR-#112 round-4 BLOCKER 1 closure: adult_content (post-handler)
    # needs the bytes to compute the F10 fingerprint for the
    # mark_complete cache overwrite, and `await request.body()` at
    # that point raises "Stream consumed" — the patched _receive was
    # drained by the handler's own body-parse step in between.
    request.state.cached_request_body_bytes = body

    return body


# ===========================================================================
# RELATED FILES:
#   __init__.py             — package marker + ASCII chain diagram
#   h5_prompt_injection.py  — outermost-safety consumer (calls this helper)
#   h4_crisis_detection.py  — relies on the cached `_body` (no replay call)
#   adult_content_output_filter.py      — inspects RESPONSE, not request; doesn't use this
#   ../../tests/test_safety_stack.py
#                          — happy-path test proves the handler still sees
#                            the body after the middleware chain runs
# ===========================================================================
