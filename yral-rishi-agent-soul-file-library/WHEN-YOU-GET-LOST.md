# WHEN-YOU-GET-LOST — yral-rishi-agent-soul-file-library

> One-line purpose: **a one-page north-star orientation for the moment you don't know where to look next.** Per B7 + F8. Optimized for ADHD + non-programmer reading.

## ⭐ The restaurant analogy

Picture this service as a **kitchen** in a busy restaurant:

- **Requests** are customer orders coming in through the door.
- **The edge Caddy on rishi-1/2** is the host who greets each customer and walks them to a table.
- **The Swarm overlay** is the dining room — orders move between tables (services) via the waitstaff (overlay traffic).
- **The 3 replicas** are 3 cooks who can each handle an order independently.
- **The middleware chain** is the prep line: each cook puts a request-ID sticker on every order, jots a note for Sentry's incident log, threads the order through the structured-logging board.
- **Postgres + Redis + Langfuse** are the pantry, fridge, and recipe binder (the stateful core, shared across all kitchens in the building).
- **The pgBouncer** is the head-of-house who batches pantry orders so the storeroom doesn't get overwhelmed.

That's the whole picture. Every file in `app/` is the cook's prep tool for ONE specific part of an order. Every doc explains a different angle on the same kitchen.

## ⭐ Are you trying to ___?

| What you're doing | Go here |
|---|---|
| Understand the service end-to-end | `DEEP-DIVE.md` (diagrams) → `WALKTHROUGH.md` (narrative) |
| Find a specific file to read | `READING-ORDER.md` (numbered with ETA + priority) |
| Look up an unfamiliar word | `GLOSSARY.md` |
| Work on the code as an AI agent | `CLAUDE.md` |
| Operate it in production | `RUNBOOK.md` |
| Check the threat model | `SECURITY.md` |
| Find a CONSTRAINTS row | `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` |
| Find the build mode + paywall/auth/mobile state | `yral-rishi-agent-plan-and-discussions/CURRENT-TRUTH.md` |
| Pick up after a context loss / laptop restart | `SESSION-2-STATE.md` then `SESSION-2-LOG.md` |
| Know what each session owns | `01-SESSION-SHARDING-AND-OWNERSHIP.md` |
| See cross-session deps and their status | `cross-session-dependencies.md` |

## ⭐ When the analogy breaks

Programming is more precise than a restaurant. A few real differences worth knowing:

- **Cooks don't share memory between orders.** Each request runs in its own asyncio task with its own ContextVar values. Cross-request state lives in Postgres or Redis, never in module-level globals (the only exception: read-only singletons like the Langfuse client).
- **The kitchen has identical layouts at 3 tables.** All 3 replicas are interchangeable. If one crashes, Swarm spins up a new identical one — no menu items lost.
- **The waitstaff is encrypted.** Inter-service traffic goes over encrypted overlays per C3. No one outside the dining room can read what's being passed between tables.
- **The pantry is shared.** All 13 services hit the same Patroni cluster + Sentinel-fronted Redis (with schema isolation per F3 + ACL isolation per Session 1's setup).

## ⭐ The fastest path back to productive

If you've been away >1 day:

1. Skim `SESSION-2-STATE.md`'s "LAST THING I DID" + "NEXT 3 PLANNED ACTIONS".
2. Read the most recent entry in `SESSION-2-LOG.md`.
3. Open `MASTER-STATUS.md` for cluster-wide context.
4. Find your task in the coordinator's most recent message (or in `cross-session-dependencies.md` if you raised one).

If you've been away >1 week or never seen this before:

1. Read `WHEN-YOU-GET-LOST.md` (this file) again — the restaurant analogy holds.
2. Read `README.md` for the doc index.
3. Read `DEEP-DIVE.md` for the diagrams.
4. Read `WALKTHROUGH.md` to connect the diagrams to source.
5. Open `app/main.py` and trace the import order at the top.

By that point you'll have the mental model back.

## RELATED FILES

- `README.md` — entrypoint
- `DEEP-DIVE.md` + `WALKTHROUGH.md` — the visual + narrative paths back to context
- `READING-ORDER.md` — numbered file list
- `GLOSSARY.md` — when a word doesn't ring a bell
- `CLAUDE.md` — if you're an AI agent picking up here

## Day-4 north star — the one sentence that ties it all together

> **This service stores Soul Files (4 layers) and assembles them into a byte-stable prompt prefix the orchestrator hands to an LLM. Layer order is part of the public contract. Byte-identity for the same (influencer, segment) is the load-bearing engineering invariant.**

If anything contradicts that sentence, the doc drifted — trust `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md` + CONSTRAINTS E8 + B4 instead.

## Day-4 quick-jump (when you forget where to look)

- "Where's the schema?" → `app/migrations/versions/001_initial_schema_and_seed.py` + `DEEP-DIVE.md`
- "Where's the composer logic?" → `app/composer/four_layer_composer.py`
- "What's the HTTP shape?" → `app/api/composed_prompt_routes.py` + `interface-contracts/01-internal-rpc-contracts.md`
- "Why does it have to be byte-identical?" → `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md` + `tests/test_composer.py::test_compose_returns_byte_identical_layered_prompt_across_reps_5x`
- "Why no auth?" → `SECURITY.md` (C3 overlay trust model + Day-4 carve-out)
- "How do I add a new archetype?" → `RUNBOOK.md`
- "What's a Layer N for?" → `GLOSSARY.md`
- "Why isn't my influencer's Soul File showing?" → no L3 row yet — Day-4.5 data port (A4 — "ALL data MUST port" from chat-ai; needs Rishi YES per A14).
