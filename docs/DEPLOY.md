# Deploy and Rollback — plain-English guide

This is the deployment guide for Rishi (non-programmer). Read this first; it explains in plain English what's happening when you "deploy" something.

## What is a "deploy"?

When you merge a PR to `main`, three things happen:

1. **GitHub runs CI** (the robot) which lints the code, runs tests, and packages it into a "Docker image" — think of it as a frozen snapshot of the new code.
2. **The image gets uploaded to GHCR** — a place that stores Docker images, like Dropbox for code packages.
3. **The running servers need to be told to use the new image.** This third step is the "deploy."

Without step 3, the new code sits in storage but isn't actually running. The website keeps serving the old code.

## How deploys work today (auto-on-merge — Path 1, 2026-06-08)

**You don't have to do anything to deploy.** When you merge a PR to `main`:

1. CI builds the new image automatically (~90 seconds)
2. The Deploy workflow automatically fires
3. The swarm rolls to the new image
4. `/health` is polled for 2 minutes after the swap
5. If `/health` returns 200 → deploy is green, you're done
6. If `/health` doesn't return 200 → **auto-rollback** kicks in automatically. The Rollback workflow runs without you doing anything. Service self-heals to the previous image.

You'll see the result in the GitHub Actions tab — green if all good, red if the auto-rollback fired. Either way, the service stays available.

### What does NOT trigger an auto-deploy

Docs-only PRs (anything that only touches `**.md`, `docs/**`, `mobile-docs-archive/**`, or `yral-rishi-agent-plan-and-discussions/**`) skip the deploy. No point rolling the servers when the runtime behavior is identical.

### What if two PRs merge close together?

A concurrency lock makes sure deploys run one at a time. If a second PR merges while the first deploy is running, the second one queues up and runs as soon as the first finishes. No overlapping deploys (which could confuse the swarm).

## The manual button (still works — emergencies only)

You can still trigger a deploy manually if you ever need to:
- Re-deploy a specific older SHA (revert a bad change without writing a new PR)
- Re-trigger a deploy after fixing an infra issue (e.g. a manager was down during the auto-deploy)

### Step-by-step (rarely needed)

1. https://github.com/dolr-ai/yral-rishi-agent/actions
2. Sidebar: **Deploy to production**
3. Top-right: **Run workflow** dropdown
4. SHA field: leave blank for latest main, OR paste a specific commit SHA
5. Click green **Run workflow**

The auto-rollback safety net applies to manual deploys too.

## How to roll back (the other button)

If a deploy breaks something — `/health` is failing, errors are climbing, mobile app is sad — you can revert.

### Step-by-step

1. Same Actions tab
2. Find **Rollback production** in the left sidebar
3. Click **Run workflow**
4. **Type a reason** for the rollback (e.g., "chat-send is returning 500 after deploy of c01ef47")
5. Click the green **Run workflow** button
6. Approve the deploy in the GitHub prompt
7. Watch the log

The rollback reverts the running servers to the previous image. **Important caveats:**
- It does NOT revert git history. The bad commit stays on `main`. You still need to fix it.
- It does NOT roll back any database changes. (Schema migrations are manual per Rule 9 anyway, so this is rarely an issue.)
- It does NOT roll back secret-mount changes (those are separate from image deploys).

## What if it fails partway through?

The workflow has built-in safety:

- It tries **all 3 swarm managers** (rishi-4, rishi-5, rishi-6). If one is down, it falls back to the next. So a leader change (rishi-4 stops being the leader) doesn't break deploys.
- It uses `--update-order start-first` — Docker Swarm starts a new container BEFORE killing the old one. So if the new image fails to start, the old one keeps serving traffic.
- It uses `--update-failure-action pause` — if any replica fails to come up, the update pauses. You'll see this in the log. **Take it as a signal that something is wrong; do NOT click Run workflow again. Investigate first.**
- After the update, it polls `/health` every 10 seconds for 2 minutes. If `/health` doesn't return 200, the workflow turns red as a signal — but the rolling restart may still be in progress. Check manually if unsure.

## One-time setup (already done for you)

The workflow needs the SSH key to be set up as a GitHub repository secret. This is one-time — done already on 2026-06-08:

- Settings → Secrets and variables → Actions → `DEPLOY_SSH_KEY` (contents of `~/.ssh/rishi-hetzner-ci-key`)

### What about the "production" environment with required reviewer?

The `production` environment we set up earlier today gated the deploy behind a manual "Approve and deploy" click. **That's now incompatible with auto-deploy** — auto-deploys would pause indefinitely waiting for approval.

**To make auto-deploy work, the workflow no longer references the `production` environment** (Path 1, 2026-06-08). The safety guards moved up to the PR stage:

1. **Branch protection on main** — CI must pass + Codex review must approve before any merge
2. **Auto-rollback on health failure** — service self-heals if a deploy breaks `/health`
3. **Manual rollback button** — you can always revert manually via the Rollback workflow

You can leave the `production` environment configuration in GitHub — it just isn't referenced by the workflow anymore. Or delete it for cleanliness. Either is fine.

## What if the workflow itself is broken?

The old manual SSH method always works as a fallback:

```
ssh -i ~/.ssh/rishi-hetzner-ci-key rishi-deploy@138.201.128.108 \
  "docker service update --image ghcr.io/dolr-ai/yral-rishi-agent:<SHA> yral-rishi-agent"
```

Replace `<SHA>` with the commit SHA you want to deploy. (You can find it in the GitHub commit history.)

Use this only if the GitHub workflow itself is failing AND you need to deploy urgently. Otherwise, fix the workflow and ship the fix through normal review.

## FAQ

**Q: Do I need to do anything to deploy when a PR is merged?**
A: Yes — merging is step 1. Deploying is step 3 (the button click). They're separate so you can choose WHEN to ship a merge. Code that's merged but not deployed sits safely on main, isn't visible to users.

**Q: What if I forget which SHA to deploy?**
A: Leave the SHA field blank — the workflow deploys the latest main HEAD by default.

**Q: Can I deploy from a feature branch?**
A: No. The "production" environment is configured to only deploy from `main`. Merge to main first.

**Q: What if I want to roll back to a specific older version, not just the immediate previous?**
A: Use the Deploy workflow + paste the older commit SHA in the input field. That's effectively a forward-deploy to an older image.

**Q: Will this auto-deploy whenever a PR merges?**
A: No. Today it's manual button-click only. We can flip on auto-deploy by changing the workflow trigger from `workflow_dispatch` to `push: branches: [main]` — but that's a separate decision and is currently off for safety.

**Q: Does the deploy roll all replicas at the same time?**
A: No. It uses Docker Swarm's rolling update (1 replica at a time by default). So the service stays available throughout — at least 1 replica is always serving traffic.

**Q: How long does a deploy take?**
A: Typically 60-120 seconds end-to-end. The image is already in GHCR; the workflow just instructs the servers to pull and swap.

## Related PROGRESS.md items

- `21α.0` (was: "Auto-deploy from CI build to swarm service") — closed by this workflow
- `21αβ.H3` (hardening-window auto-deploy mechanism) — closed by this workflow
- `21αβ.H10` (Phase 19.6 dashboard additions) — separate; this workflow doesn't add a dashboard tile yet

## Related memory

- `feedback-production-safety-strategy` — 4-layer prod safety (safe deploys / safe migrations / backup verify / monitoring). This workflow is the "safe deploys" layer.
- `feedback-agent-safety-and-24x7-access` — pipeline-as-the-safety-net philosophy.
