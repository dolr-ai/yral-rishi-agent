# Discovery Page — Search (users + influencers) — Design Addendum

**Companion to:** `docs/discovery-feed-design-2026-06-16.md`
**Date:** 2026-06-18 · design only, no code, no prod touched
**TL;DR:** Influencer search is **easy and nearly free** (the `pg_trgm` engine is
already landing in migration 042). **User search is DROPPED** (Rishi, 2026-06-18 —
not needed; v2 has no user directory anyway). Search matches **name + category +
archetype + description**; true natural-language "sentence" search is a different,
slower, paid thing we don't need (§3).

---

## 1. How we tag "user" vs "influencer" (the easy part)

Every search result carries a `kind` field: `"influencer"` or `"user"`. The search
endpoint returns one ranked list where each row is self-describing:
```json
{ "kind": "influencer", "id": "...", "display_name": "Riya", "avatar_url": "...", "subtitle": "companion · anime" }
{ "kind": "user",       "id": "...", "display_name": "Rishi", "avatar_url": "...", "subtitle": "@rishi" }
```
Mobile shows a badge/icon per `kind`, and can optionally group the list into two
sections ("Influencers" / "People"). The tagging itself is trivial — the hard part
is *having user data to search at all* (§3).

---

## 2. Influencer search — fast, and basically already set up ✅

**Engine:** `pg_trgm` (Postgres trigram fuzzy match) — **migration 042 already adds
`CREATE EXTENSION pg_trgm` + a GIN trigram index** for the feed's category signal. We
extend it to a combined searchable blob across **name + category + archetype +
description**:
```sql
CREATE INDEX idx_ai_influencers_search_trgm
    ON ai_influencers USING gin (
        LOWER(display_name || ' ' || name || ' ' || COALESCE(category,'') || ' '
              || COALESCE(archetype,'') || ' ' || COALESCE(description,'')) gin_trgm_ops);
```
So a single search box matches a bot's **name** ("riya"), its **category** ("anime",
"fitness", "food"), its **archetype** ("companion", "educator"), and words in its
**description** — all at once, ranked by best match + popularity.
**Endpoint (slots into the discovery router):**
```
GET /api/v2/discovery/search?q=<text>&kind=all|influencer|user&limit=20
```
**Ranking:** trigram similarity + a tie-break on the feed's existing popularity score
(`message_count`) so "riya" surfaces the most-used Riya first. Active bots only.

**Speed:** at our catalog scale (hundreds–low-thousands of bots) a GIN-trigram query
is **<10 ms** server-side — no extra infrastructure needed. This is the simplest
thing that's fast enough; don't over-build (no Elasticsearch, no Redis prefix tree
for v1).

---

## 3. "Just name, or category/archetype, or any sentence?" — what matches

Two different things, and the distinction matters for speed + cost:

**(a) Keyword / fuzzy match — what we build (fast, free).** The trigram index above
matches on **name, category, archetype, and description** together. So these all
work:
- `riya` → the bot named Riya
- `anime`, `food`, `fitness` → bots in that category
- `companion`, `educator` → bots of that archetype
- `cricket coach`, `cooking` → matches words in name/category/description

A **sentence** works *to the extent it shares words* with a bot's fields — e.g. "a
fitness coach for women" matches a fitness bot because "fitness"/"coach" overlap. It's
not understanding the sentence; it's matching the salient words. For real searches
("anime girl", "gym trainer", "someone to vent to" → "companion") that's plenty.

**(b) True natural-language / semantic search — NOT building (and we shouldn't).**
"Understand any sentence even with zero shared words" (e.g. "help me stop
procrastinating" → a productivity coach it shares no words with) needs **embeddings**:
embed the bot catalog AND embed the user's query, compare by meaning. That means:
- an **LLM/embedding call on every search** → slower (defeats "very very fast") and
  **costs money per query** (against the cost rule), and
- our embeddings run on **Gemini** (excluded), with no embedding model on runpod yet.

So semantic search is **slower + paid + blocked** — the opposite of what you want for
typeahead. **Recommendation: keyword search over all four fields is the right tool,
not a compromise.** If we ever want a "smart search" mode, it'd be a separate,
non-typeahead feature, and only if Saikat serves an embedding model on runpod.

---

## 4. Decisions (Rishi, 2026-06-18)
- ✅ **Influencer search only** — user search dropped (not needed; v2 has no user
  directory anyway).
- ✅ **Match name + category + archetype + description** via one trigram index.
- ✅ **Keyword/fuzzy, not semantic** — fast + free; semantic deferred indefinitely.
- The `kind` field still ships on results (always `"influencer"` for now) so a future
  user-search could slot in without a contract change.

---

## 5. "No waiting while typing" — where the speed actually comes from

Backend is <10 ms, so (same lesson as the feed) the *felt* speed is **mobile-side**:
- **Debounce ~150 ms** — wait for a tiny pause in typing before firing the request,
  so we don't fire on every keystroke.
- **Cancel in-flight requests** — when a new keystroke comes, drop the older request
  so results never arrive out of order.
- **Cache recent queries** + filter already-loaded results locally for instant
  narrowing as the user types more.
- **Short payload** — return top 20, names + avatars only.

These go in the mobile spec. The backend just needs to answer fast and consistently,
which the trigram index does.

---

## 6. Build note for Session 6
Add as one small milestone to the discovery feed dispatch (reuses migration 042's
`pg_trgm`): the combined trigram index (§2) + `GET /api/v2/discovery/search?q=&limit=`
returning the same item shape as the feed with a `kind` field. Mobile: debounce
~150 ms, cancel in-flight requests, cache recent queries (see §5). Flag-gated like the
rest. No new LLM, no new external dependency — pure SQL.
