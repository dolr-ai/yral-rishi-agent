# scripts/tests/fixtures/ — read-only test fixtures

> Each subdirectory is a frozen state validate-secrets.sh + gen-env-example.sh run against. Tests `cd` into a fixture dir + invoke the script + assert on the exit code. Nothing is written; nothing is deleted (per A1 spirit).

## Note on the `.env.local` files

Some fixtures include a `.env.local` file. The root `.gitignore` ignores `.env.local` anywhere in the tree (to prevent accidental commits of real local-dev secrets). These fixtures are intentionally exempt — they were committed via `git add -f` because:

1. The values are clearly labeled placeholders (e.g. `test-password-not-real`).
2. Without them, the test suite can't exercise the "secret present in .env.local" path.
3. The blast radius is zero: nothing in these files is a real credential.

If you add a new fixture with a `.env.local`, follow the same convention (placeholder values only) and `git add -f` it.

## Layout

- `valid/` — happy-path: well-formed `secrets.yaml` + complete `.env.local`.
- `missing-env-local/` — `secrets.yaml` exists; no `.env.local`.
- `env-local-incomplete/` — `.env.local` exists but one value is empty.
- `malformed-yaml/` — `secrets.yaml` intentionally has a YAML syntax error.
- `no-secrets-yaml/` — empty directory (only `.gitkeep`).
