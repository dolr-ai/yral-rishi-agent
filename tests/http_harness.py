"""Drive real HTTP against the real app, without Docker.

Why this exists: of 145 test files, two drove real HTTP and five touched a
real Postgres, while `routes/` sat at 22% coverage — the only layer a user can
reach, and where every bug found in September lived. Those bugs were not logic
errors deep in a service; they were a response model that rejected its own
handler's output (Sentry #602), a liveness filter missing from a query (#513),
and an auth check that ran after the body was parsed. All four are invisible to
a unit test and obvious to a request.

The seams are chosen so that as much real machinery runs as possible:

* **Routing, middleware, request parsing and `response_model` validation are
  real.** That is the whole point — #602 was a response-model failure, and only
  a real request exercises it.
* **Only the JWT signature check is stubbed.** `verify_jwt` needs an ES256 key
  from a live JWKS. `authenticated()` replaces that one function, so the real
  `get_current_user` still runs: a missing header is still a 401, a malformed
  one is still a 401, and the subject still flows through as it would in prod.
* **The database is a fake keyed on SQL fragments.** A real Postgres would be
  better and `tests/integration/` has one, but it needs Docker and therefore
  never runs on a laptop. A fake that runs everywhere and catches
  response-shape bugs beats a real one that is skipped.
"""

from contextlib import contextmanager


class FakePool:
    """An asyncpg-pool stand-in programmed by SQL fragment.

    `when("FROM ai_influencers WHERE name", row)` makes any query containing
    that fragment return `row`. Matching on a fragment rather than the whole
    statement keeps tests readable and stops them breaking when unrelated
    columns are added — the failure mode that made 127 tests break in Pass 3.
    """

    def __init__(self):
        self._rules: list[tuple[str, object]] = []
        self.executed: list[tuple[str, tuple]] = []

    def when(self, fragment: str, result):
        """Later rules win, so a test can override a fixture-level default."""
        self._rules.insert(0, (fragment, result))
        return self

    def _match(self, query, default=None):
        flat = " ".join(query.split())
        for fragment, result in self._rules:
            if " ".join(fragment.split()) in flat:
                return result() if callable(result) else result
        return default

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        return self._match(query, []) or []

    async def fetchrow(self, query, *args):
        self.executed.append((query, args))
        return self._match(query)

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return self._match(query)

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return self._match(query, "OK")

    def ran(self, fragment: str) -> bool:
        """Did any statement contain this fragment? Lets a test assert on the
        query that was actually issued — e.g. that a liveness filter is
        present — which is the #513 bug stated as a behaviour."""
        needle = " ".join(fragment.split())
        return any(needle in " ".join(q.split()) for q, _ in self.executed)


@contextmanager
def authenticated(monkeypatch, user_id="user-test-1"):
    """Make `Authorization: Bearer <anything>` resolve to `user_id`.

    Only the signature check is replaced; header parsing and the 401 paths in
    get_current_user stay real, and have their own coverage in test_auth.py.
    """
    import auth

    monkeypatch.setattr(
        auth,
        "verify_jwt",
        lambda token: {"sub": user_id, "iss": "https://auth.yral.com"},
    )
    yield {"Authorization": "Bearer test-token"}


def use_pool(monkeypatch, module, pool):
    """Point one route module's `get_pool` at a FakePool.

    Patched per-module because routes import `get_pool` by name at import
    time, so patching `database.get_pool` alone would not reach them.
    """

    async def _get_pool():
        return pool

    monkeypatch.setattr(module, "get_pool", _get_pool)
    return pool
