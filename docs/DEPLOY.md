# Deploy and Rollback — plain-English guide

This is the deployment guide for Rishi (non-programmer). Read this first; it explains in plain English what's happening when you "deploy" something.

## What is a "deploy"?

When you merge a PR to `main`, three things happen:

1. **GitHub runs CI** (the robot) which lints the code, runs tests, and packages it into a "Docker image" — think of it as a frozen snapshot of the new code.
2. **The image gets uploaded to GHCR** — a place that stores Docker images, like Dropbox for code packages.
3. **The running servers need to be told to use the new image.** This third step is the "deploy."

Without step 3, the new code sits in storage but isn't actually running. The website keeps serving the old code.

## How to deploy (the button)

The deploy workflow is at `.github/workflows/deploy.yml`. From your perspective, it's a button in the GitHub Actions tab.

### Step-by-step

1. Open the repo on GitHub: https://github.com/dolr-ai/yral-rishi-agent
2. Click the **Actions** tab at the top
3. In the left sidebar, find and click **Deploy to production**
4. Click the gray **Run workflow** dropdown button (top-right)
5. Leave the SHA field blank (this deploys the latest main) OR paste a specific commit SHA if you want a specific version
6. Click the green **Run workflow** button at the bottom
7. **GitHub asks you to approve the deploy** — click **Review deployments** → check the "production" box → click **Approve and deploy**
8. Watch the live log. It should turn green within ~2 minutes. If green = deploy succeeded; if red = something went wrong, check the log.

That's it. No SSH, no commands, no typing.

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

## One-time setup (already done for you in this PR)

The workflow needs two things to be set up in GitHub. These are one-time — you do them once and forget about them:

### Setup 1 — Upload the SSH key as a GitHub secret

The workflow needs to log into the servers via SSH. To do that, it needs the private SSH key the developer session has been using.

1. Open `~/.ssh/rishi-hetzner-ci-key` in a text editor. (On the Mac terminal: `cat ~/.ssh/rishi-hetzner-ci-key | pbcopy` copies it to clipboard.)
2. Open the repo on GitHub
3. Settings → Secrets and variables → Actions → **New repository secret**
4. Name: `DEPLOY_SSH_KEY`
5. Secret: paste the contents (the entire `-----BEGIN OPENSSH PRIVATE KEY-----` block through `-----END OPENSSH PRIVATE KEY-----`)
6. Click **Add secret**

The secret is encrypted by GitHub and only readable by workflow runs. You won't be able to view it again after saving — that's normal.

### Setup 2 — Configure the "production" environment

This is the approval gate. Every deploy (and rollback) will pause until you approve it in the GitHub UI. Without this step, the workflow would skip the approval and run immediately.

1. Settings → **Environments** → **New environment**
2. Name: `production`
3. Click **Configure environment**
4. Check **Required reviewers** → add yourself
5. (Optional) **Wait timer**: 0 minutes (no forced delay)
6. (Optional) **Deployment branches**: select "Selected branches" → add `main` only (so deploys can only run from main, not from feature branches)
7. **Save protection rules**

After this, the first deploy you trigger will show "Waiting for approval" — go to the workflow run page, click **Review deployments**, approve.

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
