# Codex Review Workflow

> Every PR opened by sessions 1-5 gets reviewed by Codex (independent of Claude) before coordinator + Rishi approve merge.

## Why Codex (not just Claude self-review)

- **Independence.** Codex reads the PR diff fresh, no chat-history bias. Claude that wrote the code may have blind spots; Codex finds them.
- **Constraint-checking.** Codex reads CONSTRAINTS.md and the session's scope file, flags violations Claude missed.
- **Style consistency.** Codex enforces the documentation standard (B7) and naming rules (B1, B5, B6) across all 5 sessions uniformly — preventing drift.
- **Second opinion on design.** When Claude makes a non-obvious design call, Codex either confirms ("this is industry-standard for X reason") or pushes back ("there's a better pattern for X").

## The full workflow (visual)

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │  STEP 1 — Session opens PR                                           │
  │  ──────────────────────────                                          │
  │                                                                        │
  │  Session 3 finishes JWT validation work, opens PR:                   │
  │    Branch: session-3/jwt-validation-with-jwks-cache                  │
  │    Target: main                                                       │
  │    Title:  "[session-3] JWT validation middleware + JWKS cache"      │
  │    Body:   PR template (see below)                                    │
  └────────────────────────────────┬────────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼────────────────────────────────────┐
  │  STEP 2 — GitHub Actions auto-fires                                  │
  │  ──────────────────────────────────                                  │
  │                                                                        │
  │  Three workflows trigger in parallel:                                │
  │    (a) lint-naming-and-comments.yml  →  enforces B1/B5/B6/B7         │
  │    (b) lint-scope-violations.yml     →  diff stays in session-3 dir   │
  │    (c) pr-codex-review.yml           →  triggers Codex review        │
  └────────────────────────────────┬────────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼────────────────────────────────────┐
  │  STEP 3 — Codex reviews                                              │
  │  ────────────────────                                                │
  │                                                                        │
  │  pr-codex-review.yml:                                                │
  │    1. Checks out the PR branch                                       │
  │    2. Computes the diff against main                                 │
  │    3. Loads context files:                                            │
  │         • CONSTRAINTS.md                                              │
  │         • Session-3's scope file                                      │
  │         • feedback_documentation_standards.md (the B7 spec)          │
  │         • interface-contracts/api-contract.md                        │
  │    4. Posts to Codex API:                                             │
  │         POST /v1/code-review                                          │
  │         { diff, context, role: "v2 reviewer", focus: [b1, b7, c6,    │
  │           e1, a8, scope] }                                            │
  │    5. Receives Codex review (inline comments + summary)              │
  │    6. Posts comments to PR via gh CLI + GITHUB_TOKEN                 │
  └────────────────────────────────┬────────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼────────────────────────────────────┐
  │  STEP 4 — Coordinator reads + summarizes                             │
  │  ───────────────────────────────────────                             │
  │                                                                        │
  │  Coordinator session (you+me) gets notified of new PR + Codex review.│
  │  Reads:                                                                │
  │    • Claude's PR description                                          │
  │    • Diff                                                              │
  │    • Codex review comments                                            │
  │  Writes a 1-paragraph summary in PR comment for Rishi:               │
  │    "Session 3 added JWT validation. Codex flagged 2 small issues:    │
  │     (a) missing role-comment on line 47; (b) suggests caching JWKS    │
  │     for 24h instead of 1h to reduce auth.yral.com load. My take:      │
  │     fix (a), keep 1h on (b) since JWKS rotation should be respected. │
  │     Recommend: fix (a) and merge. Rishi YES?"                         │
  └────────────────────────────────┬────────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼────────────────────────────────────┐
  │  STEP 5 — Rishi decides                                              │
  │  ─────────────────────                                                │
  │                                                                        │
  │  Rishi reads coordinator's summary, types one of:                    │
  │    • "YES merge"                  → coordinator merges               │
  │    • "fix and re-PR"              → session-3 fixes, opens new PR   │
  │    • "discuss with me first"      → conversation, then decision      │
  │    • "show me the full Codex review" → coordinator pastes details   │
  └────────────────────────────────┬────────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼────────────────────────────────────┐
  │  STEP 6 — Merge + auto-deploy                                        │
  │  ─────────────────────────────                                        │
  │                                                                        │
  │  Coordinator runs: gh pr merge <number> --squash                     │
  │  GitHub squash-merges PR to main                                     │
  │  Day 8+: auto-deploy workflow fires:                                 │
  │    deploy.yml: build container → push to GHCR → deploy to staging   │
  │    F4 staging is shared infra with `staging:` Redis prefix           │
  │  Manual "promote to prod" button if needed (per I3)                  │
  └─────────────────────────────────────────────────────────────────────┘
```

## The PR template (every session uses this)

Saved at `.github/PULL_REQUEST_TEMPLATE.md`:

```markdown
## What this PR does (1 sentence)
<one line, plain English, what the user-visible behavior changes>

## Session
session-N (Infra & Cluster | Template | Public-API | Orchestrator | ETL & Memory)

## Files changed (and why each)
- path/to/file.py — what role this file plays now (per B7 file-header standard)
- ...

## Constraints touched
- Lists every CONSTRAINTS.md row this PR is relevant to (e.g. "B7 doc standard, E1 latency, A8 parity")

## Scope check
- [ ] All files changed are inside session-N's owned subfolder
- [ ] No files in another session's scope or coordinator-only scope
- [ ] No files outside the monorepo root

## Test evidence
- Local tests: `pytest tests/...` → N passed
- Local docker compose: started, /health/ready returned 200
- Manual test on Motorola (Day 8+): <screenshot or N/A>
- Latency: <ms before> → <ms after>, target = 0.5 × baseline

## What might break
- Honest list of regressions this could cause
- Migration path if any
- Rollback plan in 1 sentence

## Codex review focus
Hint to Codex about what to look hardest at:
- [ ] Doc standard B7
- [ ] Naming B1/B5/B6
- [ ] No hardcoded IPs C6
- [ ] Latency E1
- [ ] Parity A8
- [ ] Scope I8/I9
- [ ] Other: ___

## Related
- Session log entry: session-logs/SESSION-N-LOG.md#YYYY-MM-DD
- Coordinator interface contract: interface-contracts/<file>.md
- Memory: feedback_<name>.md (if relevant)
```

## The GitHub Actions workflow (sketch)

`.github/workflows/pr-codex-review.yml`:

```yaml
# WHAT — Triggers a Codex review on every PR opened to main.
# WHEN — Runs on every pull_request event (open, sync, reopen).
# WHY  — Codex provides an independent second-opinion review,
#        catching things Claude missed in self-review.

name: Codex Review on PR

on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened]

jobs:
  codex-review:
    runs-on: ubuntu-latest
    steps:
      - name: Check out PR branch
        # Pull the actual code under review.
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}

      - name: Compute diff against main
        # Codex needs the diff, not the full repo.
        run: |
          git fetch origin main
          git diff origin/main...HEAD > /tmp/pr.diff
          wc -l /tmp/pr.diff

      - name: Assemble review context
        # Pull the constraint files Codex needs to know about.
        run: |
          mkdir -p /tmp/context
          cp yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md /tmp/context/
          # Infer session number from branch name
          BRANCH=${{ github.event.pull_request.head.ref }}
          SESSION_NUM=$(echo "$BRANCH" | grep -oP 'session-\K[0-9]+')
          cp yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/01-SESSION-SHARDING-AND-OWNERSHIP.md /tmp/context/

      - name: Call Codex review API
        # Send diff + context to Codex; receive structured review.
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_CODEX_API_KEY }}
        run: |
          # Pseudo: actual call shape depends on Codex API at build time
          python .github/scripts/codex-review.py \
            --diff /tmp/pr.diff \
            --context /tmp/context/ \
            --pr-number ${{ github.event.pull_request.number }} \
            --output /tmp/review.json

      - name: Post Codex review as PR comments
        # Each finding becomes a line-level comment on the diff.
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          python .github/scripts/post-codex-review.py \
            --review /tmp/review.json \
            --pr ${{ github.event.pull_request.number }}
```

## Codex API setup (one-time, before sessions start)

Rishi creates an OpenAI API key with Codex access:
1. https://platform.openai.com/api-keys → new key labeled `yral-rishi-agent-codex-review`
2. Stores in GitHub repo secret as `OPENAI_CODEX_API_KEY` (in `dolr-ai/yral-rishi-agent`)
3. Coordinator session writes the workflow + scripts above
4. Test PR opens, Codex reviews it, we tune the prompt + focus areas

## Codex review prompt (what we tell Codex about its job)

The actual prompt sent with each PR review request, stored at `.github/scripts/codex-prompt.txt`:

```
You are an independent code reviewer for the yral-rishi-agent v2 project.

Your job:
1. Read the diff in this PR.
2. Check it against the documentation standard (B7 in CONSTRAINTS.md):
   - File header block present and complete
   - Function WHAT/WHEN/WHY blocks
   - Line-level role-comments (ROLE not SYNTAX)
   - Functions in priority order
   - RELATED FILES footer
3. Check naming compliance (B1, B5, B6):
   - All identifiers read as English
   - No banned abbreviations (db, cfg, svc, etc.)
   - No utils/helpers/misc/common folders without descriptive qualifier
4. Check no-hardcoded-IPs (C6).
5. Check secrets pattern (D1, D7) — no secrets in code or images.
6. Check that the diff stays inside the session's owning scope (the
   branch name session-N tells you which session; cross-reference 01-
   SESSION-SHARDING-AND-OWNERSHIP.md).
7. Check feature parity preservation (A8) — if changing public-api,
   does it still serve every chat-ai endpoint with same JSON shapes?
8. Check latency relevance (E1) — does this change measurably affect
   the 50%-faster target? If so, is there latency evidence in PR?
9. Look for industry-standard issues: race conditions, error handling
   gaps, security smells, inefficient patterns.

Output format:
For each finding:
  - file: path/to/file:line_number
  - severity: blocker | concern | nit
  - issue: what's wrong (1-2 sentences)
  - suggestion: what to do instead (concrete)

Plus a summary at the end:
  - overall: approve | request changes | comment-only
  - top 3 things to address (if any)

Be concise. Don't restate what's good — focus on what needs change or
what needs Rishi's decision.
```

## When Codex review fires up problems

Three categories of Codex feedback, each with a different routing:

```
   ┌──────────────────────────────────────────────────────────────────┐
   │  CATEGORY A — Hard violation (blocker)                            │
   │  Example: file modified outside session scope                     │
   │           hardcoded IP in code                                     │
   │           secret committed                                         │
   │  Routing: PR auto-blocked from merge                              │
   │           Session must fix and re-PR                              │
   └──────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────┐
   │  CATEGORY B — Style / doc concern                                  │
   │  Example: missing role-comment on a non-trivial line              │
   │           function in alphabetical not priority order             │
   │  Routing: Coordinator notes in summary                            │
   │           Rishi can YES merge (and session fixes in next PR)     │
   │           OR YES merge contingent on fix in same PR              │
   └──────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────┐
   │  CATEGORY C — Design disagreement                                 │
   │  Example: Codex says "use pgvector cosine sim, not L2 distance"   │
   │           Claude used L2; both are valid                          │
   │  Routing: Coordinator surfaces both views to Rishi                │
   │           Rishi makes the call                                    │
   │           Decision documented in interface-contracts/             │
   │           Future PRs follow the locked decision                   │
   └──────────────────────────────────────────────────────────────────┘
```

## What we DON'T expect Codex to catch

- Business-logic bugs that match the spec but the spec is wrong
- Subtle latency issues only visible at scale
- UX issues (Codex doesn't see the mobile app)
- Cross-service contract gaps (interface-contracts/ docs help here)
- Things outside its training (very new libraries, our internal jargon)

These are caught by:
- Synthetic user heartbeat (H9)
- Eval harness (F14, H8)
- Rishi's Motorola testing
- Chaos tests (H3)

## Cost expectations

Codex API per PR review: ~$0.10-$0.50 depending on diff size.
20 PRs/week × 5 sessions × ~$0.30 = ~$30/week = ~$120/month.
Within "no cost controls until PMF" (E4) but flagged for visibility.
