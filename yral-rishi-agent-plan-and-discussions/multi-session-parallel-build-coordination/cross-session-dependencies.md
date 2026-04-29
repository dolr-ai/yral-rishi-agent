# Cross-Session Dependencies (kanban)
> Sessions raise OPEN deps; coordinator moves to RESOLVED when fixed. RESOLVED stays forever (audit trail).

## OPEN

<none yet — sessions will populate after launch>

---

## RESOLVED

<none yet>

---

## How to use

### Raising a dependency (session-author writes this)
```markdown
### DEP-<3-digit-number> — <short title>
Raised: YYYY-MM-DD by Session N
What:    <specific thing needed, with technical detail>
Why:     <how it unblocks or improves my work>
Blocks:  <which PRs/tasks of mine are blocked, or "no hard block">
ETA needed: <date>
```

### Resolving a dependency (coordinator writes this when fixed)
Move the entry to RESOLVED section, append:
```markdown
Resolved: YYYY-MM-DD by <who> (PR/decision link)
Resolution: <1-line: how it was answered>
```
