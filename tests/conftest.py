"""Shared pytest fixtures for the yral-rishi-agent test suite.

Placeholder for now. Wave 1 PR6 adds the testcontainers-Postgres fixtures
here — a session-scoped pgvector container, the numbered `migrations/*.sql`
applied against it, per-test truncate/reseed isolation, and an in-process
httpx client — per docs/wave1-plan-2026-07-29.md.

Note: the import path (app/, watchdog/) is configured in pyproject.toml
under [tool.pytest.ini_options] `pythonpath`, not in this file.
"""
