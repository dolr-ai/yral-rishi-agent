# ---------------------------------------------------------------------------
# validators.py — LegacyJwtValidator + StrictJwtValidator.
#
# ⭐ START HERE: two classes, same shape. Each exposes `validate(token)
# -> ValidationResult`. The dependency runs both in parallel + compares
# their `ok` flags to compute the divergence metric.
#
# THE TWO VALIDATORS:
#   LegacyJwtValidator  — what production answers from TODAY. Skips
#                         signature verification. Decodes the JWT,
#                         extracts `sub` (user_id), returns ok. Matches
#                         chat-ai's `insecure_disable_signature_validation`
#                         behavior so v2 doesn't break existing users
#                         on cutover.
#   StrictJwtValidator  — what v2 wants to answer from AFTER the shadow
#                         soak. Full JWKS RS256 signature verification,
#                         expiry check, issuer check, audience check
#                         (audience only when settings.jwt_expected_audience
#                         is set — empty default skips it).
#
# WHY ValidationResult IS A DATACLASS (not a (bool, str, str) tuple)?
# Named fields prevent the eternal "what's at index 1?" papercut. The
# dependency reads `.ok`, `.reason`, `.user_id` directly — no positional
# unpacking. Plus mypy / IDE catch field-name typos.
#
# WHY THE LEGACY VALIDATOR'S ok=True FOR ANY WELL-FORMED JWT?
# Per E9 + the JWT shadow-rollout memory: today's production traffic
# accepts ANY well-formed JWT (no sig check, no expiry check, etc.) and
# the user_id is extracted from `sub`. To not break existing users, the
# legacy validator MUST match this exact behavior. The only way legacy
# can return ok=False is on a TRULY malformed JWT (e.g., not 3 base64
# segments, or `sub` claim is missing entirely).
#
# WHY STRICT'S REASON STRINGS ARE LITERAL?
# The reason field feeds the divergence histogram on the Sentry dash;
# typed reasons mean the dashboard can pivot by reason without parsing
# free-form strings. The set is locked here + cross-referenced in
# observability.py.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from dataclasses import dataclass
from typing import Literal, Optional

import jwt

from app.api.auth.jwks_client import JwksFetchError, get_signing_keys
from app.config import get_settings


# The locked reason set the strict validator may emit. Cross-referenced
# from observability.py + the divergence histogram on the Sentry dash.
StrictFailureReason = Literal[
    "ok",                  # not a failure; sentinel for the happy path
    "malformed",           # token doesn't decode (bad base64, missing segments, etc.)
    "expired",             # exp claim in the past
    "bad_sig",             # signature didn't verify against the JWKS key
    "bad_iss",             # iss claim != settings.jwt_expected_issuer
    "bad_aud",             # aud claim != settings.jwt_expected_audience (only if set)
    "unknown_kid",         # token's kid header isn't in the current JWKS
    "jwks_fetch_error",    # JWKS endpoint unreachable / malformed response
    "missing_sub",         # token decoded but lacks the `sub` claim (no user_id)
]


# Same Literal set for legacy — it can really only return "ok" or
# "malformed" or "missing_sub" since it doesn't check anything else.
LegacyFailureReason = Literal["ok", "malformed", "missing_sub"]


@dataclass
class ValidationResult:
    """Output shape for both validators.

    WHAT: dataclass with `ok` (bool), `reason` (str — see Literal sets
          above), `user_id` (str | None — present on ok=True).
    WHEN: returned by both LegacyJwtValidator.validate() +
          StrictJwtValidator.validate().
    WHY:  named-field shape gives the dependency a clean
          `legacy.ok != strict.ok` divergence comparison without
          positional unpacking.
    """

    ok: bool
    reason: str
    user_id: Optional[str] = None


class LegacyJwtValidator:
    """Decode-without-verify validator matching chat-ai's current behavior.

    WHAT: decodes the JWT body without checking signature / expiry /
          issuer / audience. Extracts `sub` (the user_id) and returns
          ok=True. Returns ok=False only if the JWT is so malformed it
          can't be decoded at all OR if `sub` is missing entirely.
    WHEN: called by the dual-validate dependency on every authenticated
          request. Today its result is the AUTHORITATIVE answer.
    WHY:  chat-ai + Ravi's Rust service both run this way today; v2
          must match on cutover so users with already-issued tokens
          don't get a 401 surprise.
    """

    def validate(self, token: str) -> ValidationResult:
        """Decode the token; extract user_id; return result.

        WHAT: jwt.decode(..., options={"verify_signature": False,
              "verify_exp": False, "verify_iss": False, "verify_aud": False}).
        WHEN: invoked once per request from the dependency.
        WHY:  byte-equivalent to chat-ai's current call-pattern.
        """
        try:
            # `options` disables every check PyJWT does by default. The
            # algorithms list still needs something valid; "none" would
            # allow `alg: none` tokens which is a known JWT vulnerability
            # — instead we list common algorithms but disable the
            # signature check via options.verify_signature=False so the
            # algorithm restriction is moot at runtime.
            decoded = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                },
                algorithms=["RS256", "HS256"],
            )
        except jwt.PyJWTError:
            return ValidationResult(ok=False, reason="malformed", user_id=None)

        user_id = decoded.get("sub")
        if not user_id:
            return ValidationResult(ok=False, reason="missing_sub", user_id=None)

        return ValidationResult(ok=True, reason="ok", user_id=str(user_id))


class StrictJwtValidator:
    """Full RS256 + JWKS + expiry + issuer + audience validator.

    WHAT: looks up the signing key by the token's `kid` header, runs
          jwt.decode with full verification (signature + exp + iss +
          aud-if-set). Returns ValidationResult with a typed reason on
          failure.
    WHEN: called by the dual-validate dependency alongside the legacy
          validator. Today its result is SHADOW (logged but not
          authoritative); after 7-day <0.01% divergence + Rishi YES,
          becomes authoritative.
    WHY:  the v2 security posture we want — sig verify catches
          tampering; expiry catches stale tokens; iss + aud catch
          confused-deputy attacks.
    """

    def validate(self, token: str) -> ValidationResult:
        """Decode the token with full verification.

        WHAT: looks up the signing key from the cached JWKS; runs
              jwt.decode with verify_signature=True + verify_exp=True
              + verify_iss=True + verify_aud=True (when audience set);
              returns the typed result.
        WHEN: invoked once per request from the dependency, alongside
              LegacyJwtValidator.validate().
        WHY:  the auth posture v2 graduates to after the shadow soak.
        """
        # First: peek at the token's header to find the `kid` so we can
        # look up the right public key from the JWKS. Header peek is a
        # decode-without-verify of the header segment only — does NOT
        # validate the signature.
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return ValidationResult(ok=False, reason="malformed", user_id=None)

        kid = unverified_header.get("kid")
        if not kid:
            # No kid means we can't pick which key to verify against.
            # Treat as malformed for shadow-logging purposes.
            return ValidationResult(ok=False, reason="malformed", user_id=None)

        # Fetch the cached JWKS. If the JWKS endpoint is unreachable,
        # the JwksFetchError bubbles up — we catch + return
        # jwks_fetch_error so the dependency knows NOT to crash the
        # request (legacy still answers).
        try:
            keys = get_signing_keys()
        except JwksFetchError:
            return ValidationResult(ok=False, reason="jwks_fetch_error", user_id=None)

        public_key = keys.get(kid)
        if public_key is None:
            return ValidationResult(ok=False, reason="unknown_kid", user_id=None)

        settings = get_settings()

        # Build the decode options. Audience check is OPT-IN — when
        # settings.jwt_expected_audience is empty, we skip the audience
        # claim entirely. This matches chat-ai's current behavior of
        # not enforcing audience until the auth team confirms the
        # expected value.
        decode_kwargs = {
            "key": public_key,
            "algorithms": ["RS256"],
            "issuer": settings.jwt_expected_issuer,
            "options": {
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_iss": True,
                # Audience check is added below only when expected_audience is set.
            },
        }

        if settings.jwt_expected_audience:
            decode_kwargs["audience"] = settings.jwt_expected_audience
            decode_kwargs["options"]["verify_aud"] = True
        else:
            decode_kwargs["options"]["verify_aud"] = False

        try:
            decoded = jwt.decode(token, **decode_kwargs)
        except jwt.ExpiredSignatureError:
            return ValidationResult(ok=False, reason="expired", user_id=None)
        except jwt.InvalidIssuerError:
            return ValidationResult(ok=False, reason="bad_iss", user_id=None)
        except jwt.InvalidAudienceError:
            return ValidationResult(ok=False, reason="bad_aud", user_id=None)
        except jwt.InvalidSignatureError:
            return ValidationResult(ok=False, reason="bad_sig", user_id=None)
        except jwt.PyJWTError:
            # Catch-all for the remaining PyJWT errors (DecodeError,
            # InvalidTokenError, ImmatureSignatureError, etc.). The
            # specific failure type is preserved in the Sentry breadcrumb
            # that observability.py emits.
            return ValidationResult(ok=False, reason="malformed", user_id=None)

        user_id = decoded.get("sub")
        if not user_id:
            return ValidationResult(ok=False, reason="missing_sub", user_id=None)

        return ValidationResult(ok=True, reason="ok", user_id=str(user_id))


# ===========================================================================
# RELATED FILES:
#   jwks_client.py           — provides get_signing_keys() the strict path uses
#   dependency.py            — runs both validators + compares their results
#   observability.py         — emits the divergence metric to Sentry + Langfuse
#   ../../config.py          — jwt_expected_issuer + jwt_expected_audience +
#                              jwt_strict_validation_enabled
#   ../../../tests/contract/test_jwt_shadow.py
#                            — happy / expired / tampered / wrong-iss /
#                              JWKS-unreachable / flag-on smoke tests
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — E6 (auth.yral.com), E9 (shadow rollout)
# ===========================================================================
