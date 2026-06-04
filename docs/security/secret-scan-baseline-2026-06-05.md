# Gitleaks baseline — 2026-06-05

`gitleaks 8.30.1`, full history scan (`--log-opts="--all"`), 737 commits, 17.62 MB.

## TL;DR

**🟢 GREEN — zero real secrets.** 4 findings, all false positives. All 4 are test-fixture UUIDs (`550e8400-...` style placeholder UUIDs and `test-idempotency-key-001` strings) in **archived** orchestrator test files from before the v2 monolith rebuild. No real API keys, JWTs, passwords, or credentials exposed.

## Findings (4) — all false-positive

| # | File | Match | Rule | Real risk |
|---|---|---|---|---|
| 1 | `yral-rishi-agent-conversation-turn-orchestrator/tests/test_run_turn.py:1258` | `idempotency_key="550e8400-e29b-41d4-a716-446655440053"` | generic-api-key | None — test UUID |
| 2 | `yral-rishi-agent-conversation-turn-orchestrator/tests/test_safety_stack.py:744` | `shared_key = "550e8400-e29b-41d4-a716-446655440098"` | generic-api-key | None — test UUID |
| 3 | `yral-rishi-agent-conversation-turn-orchestrator/tests/test_run_turn.py:118` | `X-Idempotency-Key": "test-idempotency-key-001"` | generic-api-key | None — literal "test" prefix |
| 4 | `yral-rishi-agent-conversation-turn-orchestrator/tests/test_run_turn.py:330` | `idempotency_key="550e8400-e29b-41d4-a716-446655440013"` | generic-api-key | None — test UUID |

All four hits live in `yral-rishi-agent-conversation-turn-orchestrator/tests/` — the **archived** orchestrator directory from before the v2 monolith rebuild (per CLAUDE.md "Archive all v2 code, rebuild as monolith"). The code isn't shipped; only git history retains it.

## Recommendation

**Cutover gate S1: GREEN.** No action required. If you want a clean weekly drill output (no false-positive noise), add a `.gitleaksignore` for these 4 SHA+file pairs:

```
20ebe2577f13939160fdaf3cff98f7de57b03204:yral-rishi-agent-conversation-turn-orchestrator/tests/test_run_turn.py:generic-api-key:1258
9801855322667584ea502b8b46f341490728aa32:yral-rishi-agent-conversation-turn-orchestrator/tests/test_safety_stack.py:generic-api-key:744
7ea7e503dde283088052606b1c6b10c8c909bf12:yral-rishi-agent-conversation-turn-orchestrator/tests/test_run_turn.py:generic-api-key:118
372600dbb19c43c239677c8b5066f53ecae60145:yral-rishi-agent-conversation-turn-orchestrator/tests/test_run_turn.py:generic-api-key:330
```

I did not add the allowlist tonight because the spec was "scan + triage," not "modify repo for noise reduction." File a small follow-up PR if you want it.

## Reproducibility

```bash
gitleaks detect --source . --log-opts="--all" --report-format json --report-path /tmp/gitleaks-report.json
```
