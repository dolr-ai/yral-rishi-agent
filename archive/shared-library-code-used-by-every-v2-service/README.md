# shared-library-code-used-by-every-v2-service

**Status:** empty placeholder. Shared code (auth middleware, LLM client, Sentry wiring, Langfuse tracing, event-stream helpers, idempotency middleware, PII redaction) lives here. Every v2 service imports from this folder.

Populated during Phase 0 as the template is built. Lives in the monorepo so updates propagate to all services via normal Python package imports.
