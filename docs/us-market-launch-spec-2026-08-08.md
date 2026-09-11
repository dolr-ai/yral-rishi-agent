# US market launch — implementation spec

**Date:** 2026-08-08
**Status:** ready to build
**For:** Session 6 (backend) + mobile session + Rishi (store paperwork)

Launch YRAL in the US with 4 purpose-built SFW personas. US users see only
those 4; every other market is untouched. Paid subscription via store billing.

**Two independent tracks.** The store paperwork is the critical path and starts
today. The code can be built in parallel behind a dormant flag.

---

## Scope

**In:** market targeting on `ai_influencers`, a market filter on discovery
surfaces, `X-Market` header plumbing, subscription entitlement via RevenueCat,
4 US personas.

**Out for now** (deliberately — revisit after launch): web checkout link-out,
subscription tiers, per-market Redis feeds, market-specific ranking,
localisation beyond English.

---

## Track A — store paperwork (Rishi, starts today)

**This is the critical path. Nothing ships without it.**

1. **App Store Connect** — paid apps agreement, banking details, tax forms
   (W-8BEN-E or W-9 for the entity). If GoBazzinga hasn't signed the paid
   agreement, everything else waits.
2. **Apple Small Business Program** — one form, drops commission 30% → 15%.
   Google gives 15% on the first $1M automatically.
3. **Create two subscription products** in both consoles:
   - `yral_pro_monthly` — $12.99
   - `yral_pro_annual` — $79.99
   One entitlement: `pro`. No tiers.
4. **Set US pricing** and availability.

Subscription products are reviewed with the app build, so this gates launch.
**Realistic: 1-2 weeks to first paid US user**, bounded by paperwork and
review, not code.

---

## Track B — PR1: market column (backend)

**Rule 9: take a `pg_dump` snapshot before applying.**

`migrations/051_ai_influencers_target_markets.sql`:

```sql
ALTER TABLE ai_influencers ADD COLUMN IF NOT EXISTS target_markets TEXT[];

CREATE INDEX IF NOT EXISTS idx_influencers_target_markets
    ON ai_influencers USING GIN (target_markets);
```

**`NULL` or empty means global.** All 3,600 existing rows stay visible
everywhere with no backfill and no behaviour change. Only the 4 new US
personas get `{US}`.

Array rather than a single string, because an English persona usually serves
US + CA + UK + AU and we don't want duplicate rows.

**Config** — `app/config.py`, following the existing `_env()` pattern:

```python
# Countries whose users see ONLY personas tagged for that market.
# Empty (the default) = current behaviour everywhere. Hot-editable so the
# US feed can be switched on and off without a deploy.
MARKET_EXCLUSIVE_COUNTRIES = _env_list("MARKET_EXCLUSIVE_COUNTRIES", "")

# Header-based market override for QA. Never true in prod.
MARKET_DEBUG_OVERRIDE_ENABLED = _env_bool("MARKET_DEBUG_OVERRIDE_ENABLED", False)
```

PR1 ships the column and the config **dormant** — nothing reads them yet.

---

## Track B — PR2: market filter + header plumbing

### Resolving the user's market

Order of precedence:

1. `X-Market-Debug` header — only when `MARKET_DEBUG_OVERRIDE_ENABLED` is true
2. `X-Market` header sent by the mobile app
3. Default: unset → global behaviour

**Mobile sends the App Store / Play Store account country, not device locale.**
It's harder to spoof and it's the same value that determines pricing, so the
feed and the billing agree by construction.

We are *not* using IP geolocation in v1. `CF-IPCountry` only exists when the
hostname is orange-clouded, and the recent TLS work left things grey-clouded.
Revisit later if header spoofing turns out to matter.

### One shared helper

Put the resolution and the predicate in **one** place — `app/services/
market.py` — and call it from every discovery surface:

- `discovery_feed.build_feed_page()`
- `discovery_search`
- `recommendations`

**Do NOT filter the detail endpoint** (`GET /influencers/{id}`). The entire
acquisition funnel is social → landing page → deep link into a specific
persona. If detail respects the market filter, every deep link from a US
campaign breaks for anyone outside the US. Filter discovery, never lookup.

This is the same failure shape as the H2H list-vs-detail bug: change the shared
helper, not one endpoint.

### Where the filter goes in the feed

The feed reads a **globally precomputed list** from Redis
(`discovery_feed._read_feed_global()`, line 121) with a DB fallback
(`_fallback_active_bot_ids()`, line 472).

**When the user's market is exclusive, bypass the Redis feed entirely** and
query the DB directly:

```sql
SELECT id FROM ai_influencers
WHERE is_active = 'active'
  AND target_markets @> ARRAY[$1]::text[]
```

Do not filter the global 500-item list — with 4 personas it would usually
return an empty or near-empty page. With so few bots the ranking machinery adds
nothing; keep the session shuffle for variety and skip the rest.

### Edge case that will bite on day one

**4 personas is one page.** The user exhausts the feed immediately, and the
seen-set logic (`_seen_set_key`, `_record_seen`) will produce an empty second
page.

**Decision: cycle, don't fall back.** When an exclusive market runs out, clear
the seen set and repeat rather than backfilling with global bots — the whole
point is that a US user never sees the Indian catalogue. Confirm the mobile
infinite-scroll handles a repeating short list without visible breakage.

### Acceptance criteria

- `MARKET_EXCLUSIVE_COUNTRIES=""` → feed byte-identical to today for every user
- `MARKET_EXCLUSIVE_COUNTRIES="US"` + `X-Market: US` → exactly the 4 US personas
- `MARKET_EXCLUSIVE_COUNTRIES="US"` + `X-Market: IN` → unchanged Indian feed
- No `X-Market` header → unchanged global feed
- `GET /influencers/{id}` returns a US persona to a non-US user (deep link works)
- Search and recommendations respect the same filter as the feed
- Second feed page is non-empty for a US user

---

## Track B — PR3: subscription entitlement

`subscription_stub.py` is 20 lines — this is greenfield.

**Use RevenueCat.** Both stores, receipt validation, webhooks, entitlements.
Free under $2.5k/month. Hand-rolled IAP dies on renewals, grace periods,
billing retry, refunds and restore — that's two to three weeks we don't spend.

**Backend is one webhook.** RevenueCat fires → verify signature → flip `is_pro`
on the user row. That's the entire server side.

Migration `052_users_is_pro.sql` (or the equivalent on the existing user
table) plus `app/routes/billing.py` for the webhook, following the standard
route shape.

**The paywall — consistent with the differentiator:**

| | Free | Pro |
|---|---|---|
| Memory | 7 days | permanent |
| Proactive messages | no | yes |
| Messages | capped daily | unlimited |

Memory is the thing being sold, because persistence is what Character.AI and
Janitor can't offer. Don't gate on message count alone — that's the commodity
lever everyone else pulls.

**Not in v1:** web checkout link-out. The US Epic rulings make it viable at ~3%
versus 15%, but it's an optimisation and it can be added later without changing
anything user-facing.

---

## Track C — the 4 personas

Build per `docs/amorae-personas-mira-nyx-2026-08-02.md` — LoRA, soul file,
avatar, greeting, suggested messages.

**Tag `target_markets = '{US}'`.**

**Keep them unambiguously SFW.** IAP and App Review both depend on it, and this
is the mainstream app — the adult surface is a separate business on a separate
rail.

---

## Sequencing

| Order | Item | Blocks |
|---|---|---|
| 1 | Store paperwork (Track A) | everything |
| 2 | PR1 — column + dormant config | PR2 |
| 3 | PR2 — filter + header | flag flip |
| 4 | Mobile — send `X-Market` | flag flip |
| 5 | PR3 — RevenueCat webhook | paywall |
| 6 | Build + tag 4 personas | flag flip |
| 7 | Verify via debug override | flag flip |
| 8 | Flip `MARKET_EXCLUSIVE_COUNTRIES=US` | — |

Steps 2-6 run in parallel behind the dormant flag. Nothing user-visible
changes until step 8, and step 8 is reversible with an env change.

---

## Open questions

1. **Does GoBazzinga already have an App Store paid apps agreement?** If not,
   that's the long pole and it starts today.
2. **Do the 4 US personas overlap with Mira and Nyx**, or are they a separate
   SFW set? The persona doc specifies two semi-NSFW personas for amorae; the
   YRAL US four should be a clean SFW set.
3. **What does a US user see after exhausting 4 personas?** Spec says cycle.
   Confirm mobile handles it.
