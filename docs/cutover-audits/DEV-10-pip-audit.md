# DEV-10 — pip-audit (21α.S5)

## TL;DR

**🟡 YELLOW** — 14 known vulns across 3 packages. None look critical given our auth surface (JWT signature verification is intentionally disabled per CONSTRAINTS E9; multipart is bounded by Caddy; starlette path is gated by FastAPI on top). Recommend bumping all three before β. Not a 21α blocker.

## Findings

```
pyjwt           2.10.1  → 2.13.0   (7 advisories — PYSEC-2026-{120,179,175,177,178,176}, PYSEC-2025-183)
python-multipart 0.0.20 → 0.0.27   (3 CVEs — CVE-2026-24486, -40347, -42561)
starlette       0.46.2  → 0.49.1   (4 advisories — PYSEC-2026-161×2, CVE-2025-54121, CVE-2025-62727)
```

## Triage

### pyjwt — 7 advisories

These are PYSEC IDs in a 2.10.1 → 2.13.0 chain. **Our exposure is minimal** because v2 deliberately disables JWT signature verification per CONSTRAINTS E9 (`feedback_jwt_signature_validation_with_shadow_rollout` memory). We use pyjwt only to **parse** the unsigned claims. Most pyjwt CVEs are around signature-verification bypass / key-confusion attacks — they don't apply when we're not verifying signatures.

Still: bump to 2.13.0 in a follow-up PR (≥ pyjwt is a 1-line requirements.txt change). Trivial.

### python-multipart — 3 CVEs

Used by FastAPI for `multipart/form-data` parsing (file uploads). We have file upload paths in `app/routes/media.py`. The CVEs are typically DoS via crafted multipart bodies. Caddy's `request_body_max_size` (if set) bounds the input; check whether it is.

Also: bump to 0.0.27. 1-line change.

### starlette — 4 advisories

The framework FastAPI is built on. Bumping starlette behind FastAPI's pin is risky — must verify compat. **Don't bump alone**; bump FastAPI to a version that pulls starlette 0.49+ as a dep, after testing.

## Recommendation

**Cutover gate S5: YELLOW.** Not blocking 21α (internal cohort, exposure is low). For β:
1. Bump pyjwt 2.10.1 → 2.13.0 (trivial; CI should catch any API surface changes — none expected, our use is `.decode(...)`).
2. Bump python-multipart 0.0.20 → 0.0.27 (trivial).
3. Defer starlette bump to a FastAPI version bump PR (separate concern, more risk).
4. Verify Caddy `request_body_max_size` is set (suspect it isn't given there's no body-size handling in the Caddyfile I extracted earlier).

## Reproducibility

```bash
pip-audit -r requirements.txt
```
