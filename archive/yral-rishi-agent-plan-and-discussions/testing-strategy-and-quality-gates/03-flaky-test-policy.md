# Flaky Test Policy — 24-Hour Fix-or-Delete

> Flaky tests are worse than no tests. They train you to ignore failures. **Zero tolerance.**

## Definitions

- **Flaky test:** a test that PASSES sometimes and FAILS sometimes when nothing has changed
- **Flake threshold:** 2 failures in 7 days OR any failure that the author cannot reproduce locally on demand

## The 24-hour rule

Once a test is flagged flaky:

```
   Hour 0:  CI flakes. Author or coordinator marks the test as
            FLAKY in `tests/flaky-tests-quarantine.md`.

   Hour 0-24:  Three options, in order of preference:
   
     (a) FIX IT — diagnose root cause, eliminate the flake
         (e.g., race condition, time-dependent assertion,
          flaky external dep without proper mock)
   
     (b) MAKE IT DETERMINISTIC — add proper mocks, freeze time,
         seed random, isolate state
   
     (c) DELETE IT — if test was low-value and fixing is expensive,
         delete it and accept the coverage loss

   Hour 24:  If still flaky and not deleted → CI marks it as
            "skip with reason" so it doesn't block PRs but is
            visible in flaky-tests-quarantine.md as TECH DEBT.
   
   Hour 24+: Quarantined tests get a coordinator-tracked aging
            counter. After 14 days quarantined, they're auto-deleted
            unless explicitly resurrected.
```

## Why so strict

```
   ┌──────────────────────────────────────────────────────────────┐
   │  IF FLAKY TESTS ARE TOLERATED:                                  │
   │  ─────────────────────────                                      │
   │  • CI failures get ignored ("oh, that's a flake")              │
   │  • Real failures hide among the noise                          │
   │  • PR cycle slows (re-run CI! re-run! re-run!)                 │
   │  • Trust in the test suite erodes                              │
   │  • You stop reading failures                                   │
   │  • A real production bug ships                                 │
   └──────────────────────────────────────────────────────────────┘
```

ADHD-relevant: noisy signals are extra costly. You can't filter "this failure is real, this is flake" reliably under context-switch load.

## How CI surfaces flakes

The CI workflow records test runs. After 2 failures in 7 days:
- PR comment auto-posts: "⚠️ Test X has flaked 2x in 7d. Quarantine or fix."
- Author has 24 hours to resolve
- Coordinator escalates if author doesn't act
- Decision-log entry is written for every quarantine

## What "fix" actually means (common patterns)

```
   FLAKY PATTERN                       PROPER FIX
   ─────────────                       ──────────
   Time-dependent (sleep, now())       Freeze time with freezegun
   Random output                       Seed PRNG; use deterministic fixtures
   Network call without mock           Mock with respx or pytest-httpx
   Concurrent execution                Use asyncio.gather + assert order-free
   File-system state                   Use tmp_path fixture, not /tmp
   DB state from prior test            Use db_session fixture with rollback
   Port collision                      Use random port (port 0)
   Cache state                         Reset cache in fixture teardown
```

## What CANNOT be tested deterministically

Some things ARE inherently non-deterministic:
- LLM responses (different output even with same prompt)
- Real-network latency
- Multi-process race conditions

For these:
- Test the SHAPE not the VALUE (e.g. "response is non-empty string", not "response equals X")
- Use eval framework (Layer 7) which expects variation
- Use load tests (Layer 6) which average over runs

## The quarantine file

`tests/flaky-tests-quarantine.md` lives in the repo. Format:

```markdown
# Flaky Tests Quarantine

## Active quarantine

### test_orchestrator_handles_concurrent_turns
- Service: yral-rishi-agent-conversation-turn-orchestrator
- Quarantined: 2026-05-15 by coordinator
- Reason: race condition in test fixture, not in production code
- Fix tracking: PR #312 (in progress)
- Auto-delete date: 2026-05-29 if not fixed
- Owner: Session 4

## Resolved (kept for audit)

### test_was_flaky (resolved)
- Quarantined: 2026-05-01
- Resolved: 2026-05-03 by Session 3 (fix in PR #287)
- Lesson: pytest-httpx mock was too permissive
```

## What never gets quarantined

```
   ❌ Tests for safety/billing/auth/data-correctness paths
      These are HOT-tier (per coverage targets). A flaky test here
      means we can't trust the safety guarantee. Fix or escalate to
      Rishi for explicit YES on temporary skip.

   ❌ Contract tests vs chat-ai
      Per A8, parity is non-negotiable. Flaky parity test = parity
      itself is unstable; fix the root cause, not the test.

   ❌ Latency CI gate (E1)
      Per E1, latency regression auto-rolls-back. Flaky latency
      test would create false rollbacks; fix immediately.
```

For these categories, the rule is FIX, NEVER DELETE.

## Codex review enforcement

Codex's review prompt includes: "If this PR adds a test, check it for flake patterns (time-dependence, network without mock, race conditions)." First-defense before quarantine kicks in.
