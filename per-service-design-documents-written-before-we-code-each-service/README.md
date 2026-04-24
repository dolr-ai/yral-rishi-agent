# Each Service Design Documents — one folder per service

**Status: empty placeholder** until we architect each service in detail.

Once we start deep-diving a service (e.g., Rishi says "let's design the conversation-turn-orchestrator"), I create a folder here with the design doc, data model, API spec, test plan, open questions.

## Expected structure

```
each-service-design-documents-when-we-build-them/
├── yral-rishi-agent-public-api/
│   ├── design.md                    # Architecture, responsibilities, dependencies
│   ├── api-contract.md              # Every endpoint: path, method, request, response, errors
│   ├── data-model.md                # Postgres schema (agent_public_api schema)
│   ├── integration-points.md        # yral-auth-v2, yral-billing, all 12 internal services
│   ├── test-plan.md                 # Unit + integration + contract tests
│   └── open-questions.md
├── yral-rishi-agent-conversation-turn-orchestrator/
│   ├── design.md
│   ├── turn-lifecycle.md            # The parallel-memory-fetch + LLM-stream + async-events flow
│   ├── data-model.md
│   ├── ...
├── yral-rishi-agent-soul-file-library/
│   ├── design.md
│   ├── four-layer-composition.md   # Global / Archetype / Per-Influencer / Per-User-Segment
│   ├── versioning-and-rollback.md
│   ├── ...
├── ... (10 more)
```

## Services to eventually design here (in Rishi's priority order)

Per `refined-capability-priority-order-and-slicing/priority-order-locked-2026-04-23.md`:

1. `yral-rishi-agent-user-memory-service` (priority 1: memory + depth)
2. `yral-rishi-agent-influencer-and-profile-directory` (priority 1 + 6: memory depth uses influencer context; real-influencer parity)
3. `yral-rishi-agent-conversation-turn-orchestrator` (enables everything else)
4. `yral-rishi-agent-public-api` (exposes everything to mobile)
5. `yral-rishi-agent-soul-file-library` (priority 2: quality)
6. `yral-rishi-agent-content-safety-and-moderation` (priority 2: quality + safety)
7. `yral-rishi-agent-payments-and-creator-earnings` (priority 3: billing integration)
8. `yral-rishi-agent-proactive-message-scheduler` (priority 4 + 7: proactivity + first-turn nudge)
9. `yral-rishi-agent-skill-runtime` (priority 5: programmatic AI influencer creation via MCP)
10. `yral-rishi-agent-creator-studio` (priority 8: creator tools + analytics)
11. `yral-rishi-agent-events-and-analytics` (priority 8: shared analytics backbone)
12. `yral-rishi-agent-media-generation-and-vault` (priority 9: creator monetization + private content)
13. `yral-rishi-agent-meta-improvement-advisor` (priority 10: meta-AI advisor)

Each deep-dive is a focused conversation with Rishi — pick one, design it in one session, write the doc here. No code until overall build approval.
