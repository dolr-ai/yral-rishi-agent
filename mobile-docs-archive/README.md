# Mobile docs archive

**Purpose:** Backup non-source documentation files (planning docs, lessons, post-mortems, error logs, implementation plans) from `yral-mobile` before any PR is sent to Sarvesh for review. Sarvesh strips these files on merge, so without local backups they're lost permanently from the working tree.

## Why

Sarvesh sees plain-text docs in PRs as "personal notes" that don't belong in the mobile repo and removes them before/during merge. This loses institutional knowledge that future mobile-expert sessions need (lessons learned, design rationale, post-mortems, handoffs). Git history preserves them but requires `git log -p` archaeology — not practical for ADHD-friendly access.

**The fix:** mobile expert copies all at-risk docs to this archive folder BEFORE adding Sarvesh as reviewer on any PR.

## Process — mobile expert MUST follow before tagging Sarvesh

1. **Inventory at-risk files.** Look in:
   - `yral-mobile/docs-rishi/*.md` — primary location, ALWAYS at risk
   - `yral-mobile/*.md` at repo root (except `README.md`, `CHANGELOG.md`, `LICENSE.md`)
   - Any `HANDOFF-*.md`, `PLAN-*.md`, `POST-MORTEM-*.md`, `*-LESSONS.md`, `*-PLAN.md`, `*-NOTES.md` files anywhere in repo

2. **Create snapshot directory:** `mobile-docs-archive/PR-<number>-<short-description>-<YYYY-MM-DD>/`. Example: `mobile-docs-archive/PR-1178-h2h-chat-2026-06-03/`.

3. **Copy each at-risk file:** preserve filenames as-is so future readers find them by name.

4. **Write a `MANIFEST.md` in the snapshot directory** listing:
   - PR number and title
   - Date of snapshot (BEFORE adding Sarvesh)
   - Each file copied + 1-line description of what it contains + why it matters
   - Any docs you considered but decided NOT to back up (with reasoning)

5. **Update the master `INDEX.md` at archive root** with one row per snapshot.

6. **ONLY THEN add Sarvesh as reviewer.** Reviewer-add is the trigger for the backup process, not the merge.

## What to back up vs what to skip

| Backup | Skip |
|--------|------|
| Lessons docs (`MOBILE-EXPERT-LESSONS.md`) | Source code (`.kt`, `.swift`, etc.) |
| Planning docs (`PLAN-*.md`, `*-IMPLEMENTATION-PLAN.md`) | `README.md`, `LICENSE.md`, `CHANGELOG.md` |
| Post-mortems (`POST-MORTEM-*.md`) | `build.gradle.kts`, `Cargo.toml`, etc. |
| Handoff docs (`HANDOFF-*.md`) | `.gitignore`, `.editorconfig` |
| Planning notes (`*-NOTES.md`, `*-PLAN.md`) | Standard config files |
| Error logs / test traces if committed | Files that ARE the PR's product (UI mocks, design specs Sarvesh wants) |
| `todo.md` or session-state files | Test output Sarvesh explicitly approves |

## When in doubt

Back it up. Costs nothing; 1 hour later when Sarvesh strips it you'd wish you had.

## Baseline snapshot (2026-06-03)

`2026-06-03-baseline-snapshot/` contains all currently-at-risk mobile docs as of 2026-06-03 morning. Use as the reference for what existed before any new PR snapshots.

## Process applies to dev session too

If dev session ever opens PRs against a repo owned by someone who strips docs (currently doesn't — yral-rishi-agent is fully under our control), same process: archive non-source docs to a similar `backend-docs-archive/` folder before tagging the external reviewer.
