# scripts/tests/fixtures/ — checked-in test fixtures

> Each subdirectory is a frozen state validate-secrets.sh + gen-env-example.sh run against. Per DEP-010 tests copy a fixture dir into `mktemp -d` at runtime + rename `env.local.fixture` → `.env.local` inside the temp dir + invoke the script + assert on the exit code. The checked-in tree is read-only; the runtime copy is cleaned up via subshell EXIT trap.

## Note on the `env.local.fixture` files

Some fixtures include an `env.local.fixture` file. The repo-root `.gitignore:25` ignores the literal filename `.env.local` anywhere in the tree (to prevent accidental commits of real local-dev secrets). Per DEP-010, the literal `.env.local` filename is NEVER tracked — even for fixtures with explicit placeholder content. The previous convention (`git add -f` on a `.env.local` path) silently failed when `new-service.sh` spawned downstream services, causing red CI on 3 of 4 spawned services.

Fixtures instead ship as `env.local.fixture` — a name that matches neither `.gitignore:25` (`.env.local`) nor `:26` (`.env.*.local`). At test runtime, `test_validate_secrets.sh` copies the fixture directory into `mktemp -d` and renames `env.local.fixture` → `.env.local` inside the temp dir. The validator (which hardcodes `.env.local` as the filename it reads from cwd) sees the literal name; the checked-in tree never carries it.

If you add a new fixture, name the env-shaped file `env.local.fixture`. Do NOT use `git add -f` on a `.env.local` path — that's the DEP-010 anti-pattern.

## Layout

- `valid/` — happy-path: well-formed `secrets.yaml` + complete `env.local.fixture`.
- `missing-env-local/` — `secrets.yaml` exists; no `env.local.fixture` (and so no `.env.local` materialized at test runtime).
- `env-local-incomplete/` — `env.local.fixture` exists but one value is empty.
- `malformed-yaml/` — `secrets.yaml` intentionally has a YAML syntax error.
- `no-secrets-yaml/` — empty directory (only `.gitkeep`).
