# DEV-3 — Billing paywall verification (21α.C4)

## TL;DR

**🟡 YELLOW** — Architecture is **intentionally client-side**: mobile calls `billing.yral.com/google/chat-access/check` BEFORE sending; v2 backend trusts mobile verified access. This matches chat-ai's existing architecture (neither service has any billing call). The risk: any client (Android-bypassed, web, scraper) reaching agent.rishi.yral.com directly will bypass the gate. Acceptable for alpha (cohort is your dogfood team) but **needs a server-side check before β / production**.

## Evidence

### v2 has BILLING_URL config but ZERO call sites

```bash
$ grep -rn "BILLING_URL" app/
app/config.py:96:BILLING_URL = _env("BILLING_URL", "https://billing.yral.com")
```

The constant is defined. Nothing reads it. PROGRESS.md "Phase 1.12 ✅ Billing paywall (calls billing.yral.com)" reads as if v2 enforces — it doesn't.

### Architecture was intentionally settled — commit `7881e2e` (2026-05-26)

```
docs: Day 10 resolved — billing is client-side, no backend code needed

Mobile app calls billing.yral.com/google/chat-access/check directly.
Chat backend trusts that if a message arrives, mobile already verified
billing access. Found in yral-mobile ChatAccessBillingDataSource.kt.
```

So `BILLING_URL` constant in v2 config is dead code (or kept for symmetry with chat-ai which has the same dead config). The actual enforcement happens in `yral-mobile/.../ChatAccessBillingDataSource.kt` — out of this repo's scope.

### chat-ai matches — no server-side billing either

```bash
$ cd ~/Claude\ Projects/yral-ai-chat && grep -rln "billing|paywall|free_message|quota|credit" src/
src/services/character_generator.rs   ← false positives ("dialogue in quotation marks")
```

chat-ai's Rust source has zero quota/billing/paywall enforcement. **v2 is feature-parity with chat-ai on this**, not a regression.

### Net effect for v2 cutover

Any caller hitting `https://agent.rishi.yral.com/api/v1/chat/conversations/{id}/messages` with a valid JWT (signature unverified per CONSTRAINTS E9) gets a free reply. The Yral Metadata Server doesn't intercept; Caddy doesn't gate; v2 doesn't check.

For **21α (alpha cohort = YRAL team dogfood)**: low risk. Cohort is trusted internal users, not the public.

For **21β (Play Store + App Store)**: **NOT acceptable.** A motivated user on Android can disable the mobile check (root, modify APK, intercept the OkHttp client, hit the API direct from curl). Result: unbounded free chat → unbounded Gemini cost.

## Recommendation

**Cutover gate C4: YELLOW for 21α (acceptable), RED for 21β (must fix before production).**

The fix for β isn't trivial: server-side billing requires either:
1. **Token introspection** — every chat-send hits billing.yral.com to verify the user's quota before LLM call. Adds 50-200ms latency. The "check then act" flow has TOCTOU race issues at edges of quota.
2. **JWT claims with quota** — billing.yral.com issues JWTs with a `messages_remaining` claim. v2 reads + decrements via a fast Redis counter. Race-safe IF the JWT mints are quota-aware.
3. **Periodic reconciliation** — v2 counts messages per user per day, reports to billing.yral.com nightly. Hard fail if over a cap. Async — doesn't gate but does claw back.

Option 2 (JWT + Redis counter) is closest to the existing architecture. **Estimated ~150 LOC** to land.

This is also closely related to **DEV-12 (Phase 19.2 per-user daily LLM cost ceiling)** — that PR is about cost (not user-visible billing) but uses the same per-user Redis counter pattern. If DEV-12 lands, the substrate for option 2 above is already there.

### For the alpha go/no-go meeting tomorrow

- Confirm the alpha cohort is internal-only (YRAL team).
- Acknowledge the gap explicitly — don't surprise yourself in β with this finding.
- Track as a 21β blocker (separate from 21α gates).

## What I did NOT verify

- The mobile `ChatAccessBillingDataSource.kt` actually fires before chat-send (mobile expert can confirm)
- The billing.yral.com endpoint shape / what response triggers the mobile-side gate
- Any reconciliation mechanism that catches mobile-bypass after-the-fact
