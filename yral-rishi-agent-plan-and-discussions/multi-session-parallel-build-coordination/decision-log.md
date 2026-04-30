# Decision Log
> Append-only. The historical record of every cross-cutting decision. Most recent at TOP. Never edit past entries; correct via new entries.

## How entries look

```markdown
## YYYY-MM-DD — <decision title>

### Decision
<one sentence>

### Why
<motivation, what we considered>

### Alternative considered
<the path not taken + why>

### Decided by
<Coordinator + Rishi | Session N + Coordinator | Codex flag + Rishi>

### Affects
<services, code paths, future decisions this constrains>

### Reversibility
<Yes/No + how + cost>
```

---

## ENTRIES (most recent first)

## 2026-04-27 — Doc standard locked at option (a): B7 + F8 as written, no downgrade

### Decision
Keep the heavy doc standard exactly as locked in CONSTRAINTS B7 + F8: line-by-line role comments, 8 required docs per service, CI-enforced >50% comment density, functions in priority order, RELATED FILES footers.

### Why
Rishi is non-programmer + has ADHD. Comprehension > maintenance speed. The standard is designed specifically for him to read and understand v2 code without prior programming knowledge.

### Alternative considered
Codex (in 2nd-pass review) recommended option (b): drop line-by-line role comments, drop CI comment-density gate, reduce required docs from 8 to 5 (DEEP-DIVE / RUNBOOK / GLOSSARY required, WALKTHROUGH + WHEN-YOU-GET-LOST optional for complex services only). Coordinator also offered option (c) hybrid (line-by-line on hot-path only).

### Decided by
Rishi 2026-04-27 (explicit YES to option (a))

### Affects
- Every code file written for v2
- CI workflows (lint-naming-and-comments.yml enforces density)
- Spawn-time scaffolding (new-service.sh creates 8 STUB docs, fails CI until filled)
- Subagent definitions (per-session) reference B7 + F8 directly
- AI agents' (Claude/Codex) PR template requires comment refresh on every code change

### Reversibility
Yes — if it proves unsustainable in practice, can downgrade later. But mitigations should make it sustainable: ROLE-not-SYNTAX comments rot less; AI agents re-comment per PR; CI catches drift early.
