# 21αβ.I production-safety items — shipping log

Established 2026-06-08 from Rishi's "are we doing CI/CD right by industry standards?" question. Path 1 auto-deploy is live (per #294, #297, #298); each PR here auto-deploys after merge.

## Status

| ID | Item | PR | State | Post-deploy verification |
|---|---|:-:|:-:|---|
| I-Sec1 | gitleaks in CI | [#300](https://github.com/dolr-ai/yral-rishi-agent/pull/300) | ✅ merged 12:19 UTC | CI run on next PR shows `gitleaks secret scan` step; allowlist catches the 4 baseline FPs |
| I-Sec2 | pip-audit in CI | — | pending | TBD |
| I-Dep1 | `:stable` tag in GHCR | — | pending | TBD |
| I-Mig1 | automated pre-migration pg_dump | — | pending | TBD |
| I-Mig2 | migration linter (squawk) | — | pending | TBD |
| I-Mig3 | migration testing in CI | — | pending | TBD |
| I-Dep2 | post-deploy smoke test | — | pending (needs Rishi review of endpoint list at merge) | TBD |
| I-Dep3 | read-only SSH user separation | — | pending Rishi design review | TBD |

## I-Sec1 details

**What shipped:** `.github/workflows/security.yml` runs `gitleaks detect` on every PR and push-to-main, full git history (`fetch-depth: 0`), with `.gitleaks.toml` allowlist holding the 4 known false-positive matches from DEV-7's baseline.

**One pivot during the PR:** First CI run failed with `gitleaks-action@v2 — [dolr-ai] is an organization. License key is required.` Switched to invoking the `gitleaks` CLI binary directly (install via curl, run `gitleaks detect`). Same scan coverage, same allowlist, zero $ cost. The action was just a thin wrapper around this CLI.

**Post-deploy:** The next PR (any of the I-Sec2/Dep1/Mig* below) will exercise the gitleaks step. Look for a green ✅ `gitleaks secret scan` row in the CI checks. If a real new secret ever lands in a PR, that row goes red and the merge is blocked.
