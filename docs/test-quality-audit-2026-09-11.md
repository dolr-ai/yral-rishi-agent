# Test-quality audit — 2026-09-11

Measured, not estimated. Reproduce with
`pytest tests/ --cov=app --cov-report=term` and
`python scripts/ci/check_source_text_assertions.py`.

## Coverage: 39%, and the wrong 39%

```
                 covered/total
routes             681/2982    22%   <- the HTTP surface users touch
services          2171/4773    45%
repositories       180/436     41%
videogen           209/419     49%
eval                 0/71       0%
TOTAL             3770/9575    39%
```

Routes are the least-covered layer. That is backwards: it is the only layer a
user reaches directly, and every bug found in September lived there.

Worst, by risk (low coverage x user-facing):

| file | coverage |
|---|---|
| `routes/creator_coach.py` | 9% |
| `routes/chat.py` | 15% — the core product |
| `routes/llm_routing_admin.py` | 16% |
| `routes/influencers.py` | 20% — three bugs landed here |
| `repositories/variant_repo.py` | 0% |
| `eval/` | 0% |

## Tests that assert on our own source as text

**1,213 of 3,191 assertions (38%)** are substring matches against source read
off disk. Split by what they read:

```
510  app/ Python      <- can stay green while behaviour breaks
 51  migrations
 36  CI workflow YAML
 17  docs / config
 11  shell scripts
```

Reading a workflow YAML or a migration as text is legitimate — the file IS the
artifact and there is no behaviour to exercise. The `app/` ones are the
problem: **536 assertions** across 42 files, which is what
`scripts/ci/check_source_text_assertions.py` ratchets.

## Only 2 test files drive real HTTP; only 5 touch a real Postgres

Out of 145. The testcontainers pgvector harness in `tests/conftest.py` has
existed since July and is used by four files.

```
HTTP: tests/test_21g_P34_M0_discovery_pins.py
      tests/test_validate_generate_null_reason.py
DB:   tests/integration/{surface_column,target_markets_column,
                         schema_and_health,deleted_persona_name_reuse}.py
```

## This is not theoretical

| bug | what the tests did |
|---|---|
| PR #503 | source-text test stayed green; the author edited the assertion to match the break |
| PR #501 -> Sentry #602 | valid-concept path 500'd for four days; `influencers.py` is 20% covered |
| Issue #513 | `get_by_name` had no test at all |
| PR #514 | the `unban` bug was found by reading the code, not by a test |

## What shipped from this audit

1. **Source-text ratchet** — `scripts/ci/check_source_text_assertions.py`, wired
   into the lint job. Blocks NEW source-text assertions against `app/`. Baseline
   536, may fall, never rise.
2. **Coverage ratchet** — `--cov-fail-under=40` in the test job. A floor against
   backsliding, not a target to chase. 40 is CI's number, not a laptop's: the
   integration tests need Docker, so a local run reports ~39.4% and CI 40.06%.
   Calibrate the floor from CI.

## What to do next, in order

3. **Build the HTTP + real-DB harness properly.** `TestClient` plus the existing
   testcontainers Postgres. `tests/test_validate_generate_null_reason.py` is the
   shape: it drives the real route and fails on the unfixed code. Applying that
   to `routes/` is what moves 22%.
4. **Convert by risk, not by count.** Do not touch 536 assertions. Target
   `chat.py`, `influencers.py`, `creator_coach.py` — roughly 120 assertions that
   matter more than the other 400.
5. **Decide whether `eval/` and `variant_repo.py` are live code.** Both 0%. If
   they are dead, deleting beats testing.

## What NOT to do

Chase a coverage percentage. 39% -> 80% as a goal produces tests written to
touch lines. The measure that matters is whether a broken behaviour fails a
test — in practice, whether a new test fails BEFORE the fix is applied.
