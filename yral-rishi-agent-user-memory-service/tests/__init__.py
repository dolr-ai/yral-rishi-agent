# tests/__init__.py — package marker for the test suite.
#
# ⭐ START HERE: nothing executable here. Python requires this file to
# treat `tests/` as an importable package. All test logic lives in the
# `test_*.py` files in this folder.
#
# CURRENT TESTS:
#   conftest.py                 — shared fixtures (testcontainers-postgres,
#                                 Alembic upgrade, asyncpg pool)
#   test_schema_migrations.py   — Alembic round-trip up/down test
#
# DELIVERABLE 2 (next PR) WILL ADD:
#   test_conversation_routes.py — RPC endpoint tests (POST/GET conversations
#                                 and messages)
