# ---------------------------------------------------------------------------
# app/middleware/__init__.py — package marker for the safety-stack middleware.
#
# ⭐ START HERE: this package contains the three Day-3 safety
# middlewares that sit IN FRONT OF the `POST /v1/turn` route handler:
#
#   h5_prompt_injection.py  — rule-based jailbreak / role-override / base64 detection
#   h4_crisis_detection.py  — keyword-based self-harm / crisis-language detection
#   a10_adult_content_filter.py      — output-side NSFW filter on the handler's response
#
# Plus two PRIVATE helpers (leading underscore so they don't accidentally
# get imported by other services):
#
#   _safety_audit.py        — ContextVar-based audit trail (production no-op;
#                              tests inject a list to verify request flow order)
#   _body_replay.py         — read the request body in middleware + replay it
#                              via a custom receive callable so downstream
#                              layers re-read the same bytes
#
# WHY `BaseHTTPMiddleware` AND NOT PURE-ASGI?
# Each middleware's logic — path-filter, gate-check, body-read,
# pattern-match, short-circuit JSONResponse — fits cleanly inside
# `BaseHTTPMiddleware.dispatch(request, call_next)`. The pure-ASGI
# `__call__(scope, receive, send)` form would be more verbose for zero
# functional gain at Day-3 scope. Per A2.1, the simpler interface wins.
# If a future safety layer needs to STREAM responses or buffer them in
# pieces, that layer's middleware can drop to pure-ASGI without
# disturbing the other layers.
#
# WHY THREE FILES INSTEAD OF ONE `safety_middleware.py`?
# Per the Day-3 directive verbatim: "Each middleware is its own file
# under app/middleware/{h5_prompt_injection,h4_crisis_detection,
# a10_adult_content_filter}.py." Splitting also means a future PR that
# replaces (say) H5's rule-based detector with an ML classifier
# touches ONE file, not a 3-class kitchen-sink module.
#
# REQUEST FLOW THROUGH THIS PACKAGE (decided in app/main.py):
#
#                   ┌── RequestIdMiddleware ────────────┐
#                   │   (outermost — assigns request id) │
#                   │  ┌── H5PromptInjectionMiddleware ─┐│
#                   │  │   (outermost safety layer)    ││
#                   │  │  ┌── H4CrisisDetectionMiddleware ─┐
#                   │  │  │   (crisis-routing)        ││ │
#                   │  │  │  ┌── A10AdultContentFilterMiddleware ─┐
#                   │  │  │  │   (output-side filter) ││ │ │
#                   │  │  │  │  ┌── run_turn handler ─┐│ │ │
#                   │  │  │  │  │   (innermost)      │││ │ │
#                   │  │  │  │  └────────────────────┘││ │ │
#                   │  │  │  └────────────────────────┘│ │ │
#                   │  │  └─────────────────────────────┘ │ │
#                   │  └─────────────────────────────────┘ │
#                   └───────────────────────────────────────┘
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   _safety_audit.py        — production-no-op audit-trail helper
#   _body_replay.py         — shared body-read + receive-replay helper
#   h5_prompt_injection.py  — H5 layer
#   h4_crisis_detection.py  — H4 layer
#   a10_adult_content_filter.py      — A10 layer
#   ../main.py              — mounts all three (LIFO add order documented there)
#   ../safety/canned_responses.py
#                          — what each layer returns when it short-circuits
#   ../../tests/test_safety_stack.py
#                          — full coverage of the three layers + order verification
# ===========================================================================
