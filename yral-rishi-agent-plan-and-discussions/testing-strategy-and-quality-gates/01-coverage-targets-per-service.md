# Coverage Targets Per Service (locked 2026-04-29)

> Floors, not ceilings. CI blocks merge if coverage REGRESSES below floor on a PR.

## The targets

| Service | Tier | Floor | Why |
|---|---|---|---|
| `yral-rishi-agent-public-api` | HOT | 75% | Auth + billing check + routing — security/financial |
| `yral-rishi-agent-conversation-turn-orchestrator` | HOT | 75% | The brain — latency + correctness critical |
| `yral-rishi-agent-content-safety-and-moderation` | HOT | 80% | Regulatory + crisis detection — non-negotiable |
| `yral-rishi-agent-payments-and-creator-earnings` | HOT | 80% | Money — every bug is a financial bug |
| `etl-scripts/` | HOT | 80% | A4 says no row dropped — verify exhaustively |
| `yral-rishi-agent-soul-file-library` | WARM | 60% | Quality matters but not security |
| `yral-rishi-agent-user-memory-service` | WARM | 60% | Memory bugs degrade UX, don't crash service |
| `yral-rishi-agent-influencer-and-profile-directory` | WARM | 50% | CRUD — well-trodden patterns |
| `yral-rishi-agent-skill-runtime` | WARM | 50% | MCP integration is mostly framework code |
| `yral-rishi-agent-creator-studio` | COOL | 35% | Internal-facing; no end-user impact |
| `yral-rishi-agent-events-and-analytics` | COOL | 30% | Analytics drift is recoverable |
| `yral-rishi-agent-proactive-message-scheduler` | COOL | 35% | Scheduler bugs delay sends, don't break chat |
| `yral-rishi-agent-media-generation-and-vault` | COOL | 35% | Async; failures retry-safe |
| `yral-rishi-agent-meta-improvement-advisor` | COOL | 25% | Internal only; you're the only user |

## What counts as covered

- Lines executed by tests (pytest-cov standard)
- Excludes: imports, type hints, `if __name__ == "__main__"`, abstract methods
- Excludes: pure data classes (Pydantic models without validators)

## What's tested vs what's covered

These are different. Coverage = lines hit. Quality = correct behavior tested.

```
   COVERED but not properly TESTED:
     def add(a, b):
         return a + b
     # test
     def test_add():
         add(1, 2)  # ← coverage 100%, asserts NOTHING

   COVERED AND TESTED:
     def test_add_returns_sum():
         assert add(1, 2) == 3
         assert add(-1, 1) == 0
         assert add(0, 0) == 0
```

Codex review catches coverage-without-testing patterns. Tautological tests get rejected.

## CI gate behavior

```
   PR submitted → run pytest --cov
        │
        ├─ Coverage on touched files DROPS below floor → ❌ FAIL, block merge
        │
        ├─ Coverage on touched files STAYS or RISES → ✅ PASS
        │
        ├─ Coverage on touched files BELOW floor but not regressed → ⚠️ WARN
        │   (sometimes acceptable; coordinator + Codex decide)
        │
        └─ Per-PR delta posted as comment for Rishi to read at-a-glance
```

## When floors get adjusted

A floor can be lowered (e.g., HOT 75% → 70%) only via:
1. Coordinator proposes the change with reason
2. Codex reviews the proposal
3. Rishi types YES
4. CONSTRAINTS J1 row is updated
5. Decision logged in `decision-log.md`

Never lower floors silently to "make CI pass."

## When floors get raised

If a service consistently hits 90% naturally, we leave it. Don't raise floors to chase ceilings — that's the vanity metric trap.

## Special cases

- **Generated code** (e.g., from OpenAPI schemas) — excluded from coverage
- **Migrations** — covered by H11 (schema migration safety net), not pytest coverage
- **Bash scripts** in `bootstrap-scripts-...` — manual chaos tests, not pytest
- **Test files themselves** — never counted toward coverage

## The "WARN" zone use case

Sometimes a refactor temporarily drops coverage below floor (e.g., big rewrite, new code path). Coordinator can grant a 1-PR exception with `coverage-exception: <reason>` label, but the NEXT PR must restore floor.

This is the escape valve for legitimate refactors without burning velocity.
