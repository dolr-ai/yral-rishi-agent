## What this PR does (1 sentence)
<one-line plain-English summary>

## Session
<!-- which session is the author? e.g. session-1, session-2, session-5, coordinator -->
session-N

## Files changed (and why each)
<!-- list each significant file + the role it plays per B7 file-header standard -->
- path/to/file.py — what role this file plays now

## Constraints touched
<!-- list every CONSTRAINTS.md row this PR is relevant to -->
- e.g. B7 doc standard, E1 latency, A8 parity

## Scope check
- [ ] All files changed are inside the owning session's subfolder (per I8)
- [ ] No files in another session's scope
- [ ] No CONSTRAINTS.md / README.md / TIMELINE.md edits (coordinator-only)
- [ ] No memory file writes (`~/.claude/projects/`)
- [ ] No deletions (per A1)

## Test evidence
<!-- per J1-J6 testing strategy -->
- Local tests: `pytest tests/...` → N passed
- Coverage delta: hot/warm/cool tier, before → after
- Local docker compose: started OK, /health/ready returned 200
- Manual test on Motorola (Day 8+): <screenshot or N/A>
- Latency: <ms before> → <ms after>, target = 0.5 × Sentry baseline

## What might break
<!-- honest list of regressions this PR could cause -->
-

## Rollback plan
<!-- one sentence: what undoes this PR if needed -->
`gh pr revert <number>` reverts cleanly; no schema migrations or external state to undo.

## Codex review focus
<!-- hint to Codex about what to look hardest at -->
- [ ] Doc standard B7 (file header + function WHAT/WHEN/WHY + line role-comments)
- [ ] Naming B1 / B5 / B6 (English readable, no banned abbreviations)
- [ ] No hardcoded IPs C6
- [ ] Latency E1 (≥50% faster than Python chat-ai)
- [ ] Parity A8 + A16 (same JSON shapes as chat-ai)
- [ ] Scope I8 / I9 (stays in own session subfolder)
- [ ] Test quality J1 / J3 (no tautological tests, no flake patterns)
- [ ] Secrets D1 / D8 (no values in code, manifest updated)
- [ ] Other: ___

## Auto-merge eligibility (per I14)
<!-- coordinator decides; author flags if applicable -->
- [ ] PR is .md-only / test-only / lint-only / comment-update-only
- [ ] Diff < 200 lines
- [ ] No critical-scope files (.github/, secrets, CONSTRAINTS, memory)
<!-- If all above + Codex APPROVE + CI green → coordinator auto-merges per I14 -->

## Related
<!-- pointers for coordinator + Rishi + Codex -->
- Session log entry: session-logs/SESSION-N-LOG.md (date)
- Interface contract: interface-contracts/<file>.md (if API changed)
- Memory: feedback_<name>.md (if applicable)
- Decision-log entry: decision-log.md (if a binding decision was made)
