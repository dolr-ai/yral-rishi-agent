# READING-ORDER.md — what to read first when you open this service

## If you're brand new to this service (5-minute orientation)

1. `README.md` — what this service does + Phase 1 scope (1 min)
2. `WHEN-YOU-GET-LOST.md` — north-star orientation + restaurant analogy (2 min)
3. `app/migrations/versions/001_initial_schema.py` — the schema (2 min)

## If you're debugging a production issue

1. `RUNBOOK.md` — operator procedures + known failure modes
2. `app/database.py` — asyncpg pool (most DB issues start here)
3. `app/main.py` — lifespan startup + health routes

## If you're adding a new feature

1. `DEEP-DIVE.md` — architecture decisions + data model rationale
2. `app/migrations/versions/001_initial_schema.py` — existing schema
3. `app/migrations/env.py` — how Alembic runs migrations
4. `app/config.py` — where to add new config fields
5. `WALKTHROUGH.md` — trace a request end-to-end (after Deliverable 2)

## Full reading sequence (deep understanding)

1. `README.md` — overview
2. `CLAUDE.md` — AI agent rules (read before editing any code)
3. `GLOSSARY.md` — domain terms
4. `WHEN-YOU-GET-LOST.md` — orientation
5. `project.config` — Postgres schema/role names
6. `secrets.yaml` — secret declarations
7. `pyproject.toml` — Python deps
8. `Dockerfile` — two-stage build
9. `docker-compose.yml` — local dev stack
10. `app/__init__.py` — package marker (trivial)
11. `app/config.py` — Settings singleton ⭐
12. `app/database.py` — asyncpg pool lifecycle ⭐
13. `app/main.py` — FastAPI entry point
14. `app/migrations/env.py` — Alembic env
15. `app/migrations/versions/001_initial_schema.py` — schema ⭐⭐
16. `tests/conftest.py` — testcontainers fixtures
17. `tests/test_schema_migrations.py` — migration round-trip test
18. `DEEP-DIVE.md` — architecture deep-dive
19. `WALKTHROUGH.md` — user-action trace (Deliverable 2)
20. `RUNBOOK.md` — operator procedures
21. `SECURITY.md` — security model

⭐ = most important file to understand; ⭐⭐ = the heart of Phase 1
