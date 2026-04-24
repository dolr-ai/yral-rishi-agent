# Plan and Constraints Documents

This folder **mirrors** the canonical GitHub plan repo `dolr-ai/yral-rishi-agent-plan`. The primary working copy is at `~/Claude Projects/yral-rishi-agent-plan/` (git-backed, synced with GitHub).

Once we formally move the plan repo under this umbrella, this folder will BE the git repo. Until then, it's a pointer.

## Files in the plan repo (to reference)

- `README.md` — the big plan (~1500 lines): vision, capability blueprints A-H, 13 services, roadmap, mobile audit, memory index
- `V2_TEMPLATE_AND_CLUSTER_PLAN.md` — template + cluster design (Swarm-only networking, node role layout, CI guardrails)
- `CONSTRAINTS.md` — tight reviewable ledger of every hard rule, 55+ rows across 9 categories

## To read the plan

```bash
cd "/Users/rishichadha/Claude Projects/yral-rishi-agent-plan/"
cat README.md              # the big plan
cat CONSTRAINTS.md         # the hard rules
cat V2_TEMPLATE_AND_CLUSTER_PLAN.md  # template + cluster
```

Or online: https://github.com/dolr-ai/yral-rishi-agent-plan (private)

## Moving the plan repo under this umbrella (future decision)

Option A: leave plan repo at `~/Claude Projects/yral-rishi-agent-plan/` (current).
Option B: move plan repo to `~/Claude Projects/yral-rishi-agent/plan-and-constraints-documents/` (clean umbrella). Git + GitHub connection preserved; no-delete covenant respected.

**Deferred decision — ask Rishi when natural.** Both paths work. Option B is cleaner long-term; Option A minimizes disruption.
