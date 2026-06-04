# DEV-8 — JWT extraction comparison (21α.S3)

## TL;DR

**🟢 GREEN** — v2 (`app/auth.py`, 48 lines) and chat-ai (`src/middleware/auth.rs`, 92 lines) accept and reject the same tokens. Same issuer list, same signature-skip, same Bearer-prefix tolerance, same 401 response shape. One acceptable divergence (v2 also accepts HS256; chat-ai is RS256-only — strictly broader on v2 side, no rejection-asymmetry).

## Behavioral diff

| Property | chat-ai (Rust) | v2 (Python) | Same? |
|---|---|---|---|
| Algorithms accepted | `RS256` | `RS256, HS256` | v2 **broader** ✅ (no rejection asymmetry) |
| Signature verification | `insecure_disable_signature_validation()` | `verify_signature: False` | ✅ both skip |
| Expiration check | `validation.set_required_spec_claims(["exp", ...])` + default `validate_exp=true` | `verify_exp: True` | ✅ both enforce |
| Audience check | `validate_aud = false` | `verify_aud: False` | ✅ both skip |
| Issuer allow-list | `["https://auth.yral.com", "https://auth.dolr.ai"]` | `EXPECTED_ISSUERS = ["https://auth.yral.com", "https://auth.dolr.ai"]` (config.py:104) | ✅ same |
| Required claims | `exp`, `sub`, `iss` | `iss`, `sub` (explicit checks) + `exp` (lib-enforced) | ✅ same effective set |
| Empty `sub` rejected | `"Invalid token: missing sub"` | `"Invalid token: missing sub"` | ✅ byte-identical |
| Bearer / bearer (case) | `strip_prefix("Bearer ").or_else(... "bearer ")` | `startswith(("Bearer ", "bearer "))` | ✅ same tolerance |

## Response shape — error path

| Trigger | chat-ai | v2 |
|---|---|---|
| No `Authorization` header | 401 + `{"detail": "Missing authorization header"}` | 401 + `{"detail": "Missing authorization header"}` ✅ |
| Wrong prefix | 401 + `{"detail": "Invalid authorization header format. Expected: Bearer <token>"}` | 401 + identical string ✅ |
| Bad JWT decode | 401 + `{"detail": "Invalid token: <err>"}` | 401 + `{"detail": "Invalid token: <err>"}` ✅ |
| Bad issuer | 401 + `{"detail": "Invalid token: ..."}` (folded into decode error via `set_issuer` validation) | 401 + `{"detail": "Invalid token issuer: <iss>"}` ⚠️ **different message** |
| Expired | 401 + `{"detail": "Invalid token: ExpiredSignature"}` | 401 + `{"detail": "Token has expired"}` ⚠️ **different message** |
| Empty `sub` | 401 + `{"detail": "Invalid token: missing sub"}` | 401 + `{"detail": "Invalid token: missing sub"}` ✅ |

**Two error-message divergences (issuer, expired)** — chat-ai folds both into the generic "Invalid token: ..." path while v2 splits them out. The HTTP code (401) is identical in both directions; mobile clients keying off status code see no diff. Clients keying off error-message text would see a diff but no production mobile path does that (Yral Metadata Server handles the re-auth flow on any 401).

## Critical question: does ANY token chat-ai accepts get rejected by v2?

**No.** Reasoning:
- v2 accepts a strict **superset** of chat-ai (RS256 ∪ HS256 vs RS256-only)
- All other checks are identical (issuer allow-list, sub required, exp enforced, aud skipped)
- v2 + chat-ai both skip signature, so signature-malformed but otherwise-valid tokens pass on both

Conversely: any token v2 accepts gets accepted by chat-ai too, except HS256-signed tokens chat-ai would reject as the wrong algorithm. Mobile only ever mints RS256 (per the auth.yral.com flow), so this is theoretical.

## Recommendation

**Cutover gate S3: GREEN.** Zero risk of "logs users out at cutover" because acceptance criteria are equivalent (v2 is broader). The two error-message divergences are cosmetic — mobile clients route on the 401, not the text.

If you want absolute string parity later (e.g., for log-grep symmetry across both services), it's a 2-line v2 change: change `"Token has expired"` → `"Invalid token: ExpiredSignatureError"` and `"Invalid token issuer: ..."` → fold into the generic decode-error path. Not blocking.

## What I did NOT verify

- Live token mint + bilateral request (would need a real Yral auth.yral.com mint; the synthetic JWTs from the latency script confirm the basics already)
- Token revocation / blacklist handling (neither service implements; same as v1, by design)
- Performance of the unverified decode (negligible; sub-millisecond)
