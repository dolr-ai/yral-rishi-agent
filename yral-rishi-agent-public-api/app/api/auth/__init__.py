# ---------------------------------------------------------------------------
# app/api/auth/ — JWT authentication shadow rig per CONSTRAINTS E9.
#
# ⭐ START HERE: this package implements DUAL-VALIDATE for JWTs. Every
# authenticated request runs BOTH a LEGACY validator (byte-equivalent to
# chat-ai's current behavior — accept any well-formed JWT without
# signature verification) AND a STRICT validator (full JWKS-based
# signature verification + expiry + issuer + audience checks). Today
# LEGACY is authoritative; STRICT runs in shadow + its result is logged
# to Langfuse + Sentry. Once shadow soak shows <0.01% divergence for
# 7 consecutive days (per E9 + the JWT shadow-rollout memory + Rishi
# typed YES), the `jwt_strict_validation_enabled` flag flips True and
# STRICT becomes authoritative.
#
# PACKAGE CONTENTS:
#   jwks_client.py    — fetch + cache JWKS from auth.yral.com (6h
#                       in-process TTL per Rishi's Day-3 directive;
#                       Day-4 may promote to Redis-shared)
#   validators.py     — LegacyJwtValidator (skip-sig) + StrictJwtValidator
#                       (full RS256 verify); both return a typed result
#                       dict so the dependency can compare them
#   observability.py  — emits the divergence metric to Sentry + Langfuse
#                       so divergence rate is visible on dashboards
#   dependency.py     — `authenticate_user_dual_validate` — the single
#                       FastAPI dependency every authenticated endpoint
#                       SHOULD depend on once Day-4 wires it in. Day-3
#                       leaves the rig SELF-CONTAINED + exercised via
#                       a test-internal endpoint (per the agent
#                       definition Day 3 scope guardrail).
#
# WHY SHADOW MODE INSTEAD OF "JUST FLIP THE SWITCH"?
# Both Ravi's Rust yral-ai-chat AND our Python yral-chat-ai today have
# `insecure_disable_signature_validation` ON. If v2 flipped strict
# verification ON at deploy, EVERY user with a token chat-ai accepted
# (but strict would reject — e.g., expired-but-still-cached tokens,
# or tokens with a different issuer-format from what we expect) would
# get a 401 on their next request. The shadow path lets us see exactly
# how many users would have been affected — BEFORE the flip — so we
# can either fix the cause (e.g., correct the expected_issuer) or
# coordinate a mobile-side token-refresh push BEFORE flipping.
#
# WHY DAY-3 DOES NOT WIRE THE DEPENDENCY INTO REAL HANDLERS?
# Per the agent definition Day 3 scope guardrail: "ONLY auth dependency
# + JWKS client + the feature flag. Do NOT touch handlers or DTOs."
# Day-4 wires this dependency into the real chat / influencer handlers
# as part of the orchestrator RPC integration. Day-3 ships the rig +
# exercises it via test-internal endpoints so the contract is locked
# down before the wiring happens.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Empty package marker. Submodules import what they need explicitly.

# ===========================================================================
# RELATED FILES:
#   ../../config.py          — defines jwt_strict_validation_enabled,
#                              jwks_url, jwt_expected_issuer,
#                              jwt_expected_audience, jwks_cache_ttl_seconds
#   jwks_client.py           — JWKS fetch + per-replica cache
#   validators.py            — LegacyJwtValidator + StrictJwtValidator
#   observability.py         — Sentry + Langfuse emission helpers
#   dependency.py            — the FastAPI dependency Day-4 wires in
#   ../../../tests/contract/test_jwt_shadow.py
#                            — happy / expired / tampered / wrong-iss /
#                              JWKS-unreachable / flag-on smoke
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — E6 (auth via auth.yral.com),
#                              E9 (shadow rollout mandate)
# ===========================================================================
