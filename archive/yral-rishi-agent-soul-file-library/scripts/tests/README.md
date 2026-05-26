# scripts/tests/ — shell-test suite for the D8 bridge scripts

> Plain-bash test scripts that exercise `validate-secrets.sh` + `gen-env-example.sh` against read-only fixture directories. Per A1 spirit no test creates and then deletes temp state; everything happens against pre-committed fixtures.

## How to run

```bash
# From the template root:
bash scripts/tests/test_validate_secrets.sh
bash scripts/tests/test_gen_env_example.sh
```

Each script prints PASS/FAIL per case and exits non-zero if any case failed. The CI workflow runs both as part of the `shell-tests` job.

## Coverage

| Script | Covered | Not covered |
|---|---|---|
| `validate-secrets.sh` | local-env-var path (happy + 4 failure paths) | `gh secret list` integration — needs live auth |
| `gen-env-example.sh` | `--check` mode + output structure | actual file write — exercised by PR 5 (hello-world spawn) |
| `sync-github-secrets.sh` | NONE | interactive + real gh push; defer to manual testing |

## Why no `sync-github-secrets.sh` automated test

The script is interactive (hidden-input prompt) and writes to real GitHub Secrets. There's no good way to automate without either:
- Mocking `gh` via PATH manipulation — adds complexity for marginal coverage.
- Running against a throwaway GitHub repo + real auth — out of scope for unit-level tests.

Manual smoke procedure documented in `../sync-github-secrets.sh`'s file header. Live smoke happens at PR 5 (hello-world spawn) when the script seeds the spawned service's GitHub Secrets.

## Fixtures

All in `fixtures/<name>/`:

- `valid/` — happy-path: `secrets.yaml` declares two secrets, `.env.local` populates both with non-empty values.
- `missing-env-local/` — `secrets.yaml` declares one required-local secret; no `.env.local`.
- `env-local-incomplete/` — `.env.local` exists but one value is empty.
- `malformed-yaml/` — `secrets.yaml` intentionally has a YAML syntax error.
- `no-secrets-yaml/` — empty directory (only `.gitkeep`).

Every fixture uses `required_in: [local]` only, so tests are self-contained (no `gh` CLI auth needed).

## Adding a new test case

1. Drop a fixture into `fixtures/<descriptive-name>/`.
2. Add an `assert_exit_code` (or `assert_output_contains`) call to the relevant test script.
3. Run the test script locally to confirm PASS before opening a PR.

## RELATED FILES

- `../validate-secrets.sh` — script under test
- `../gen-env-example.sh` — script under test
- `../sync-github-secrets.sh` — not automated; see file header for manual smoke
- `../../.github/workflows/per-service-ci.yml` — runs these tests in CI
- `../../yral-rishi-agent-plan-and-discussions/testing-strategy-and-quality-gates/` — J1-J6 testing pyramid

## Status

Scaffold. Coverage grows as new D8 patterns get exercised (PR 5's hello-world spawn will surface gaps; live gh integration test follows when we want it).
