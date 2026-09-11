"""Every route either requires a caller, or is on a list that says why not.

A route shipped without its auth call looks fine in review and is invisible
to unit tests. This walks the *published* route table — so a new endpoint is
covered the moment it is registered, with no test to remember to write — and
asserts each one refuses an anonymous caller.

The allowlist is the point of the test. Adding an entry is a deliberate,
reviewable statement that a path is meant to be public; the test fails until
someone does that, which is the prompt to think about it.
"""

import pytest
from fastapi.testclient import TestClient

# Paths that are public by design. Anything not here must reject an anonymous
# caller. Keep the reason attached — it is what a reviewer needs.
PUBLIC = {
    "/health": "liveness probe — must answer before auth exists",
    "/health/live": "liveness probe",
    "/status": "public status page — same family as /health",
    "/openapi.json": "the published spec; generated clients fetch it",
    "/docs": "swagger UI",
    "/redoc": "redoc UI",
    "/docs/oauth2-redirect": "swagger UI plumbing",
    "/": "root",
    "/api/v2/discovery/influencer-feed": "JWT optional by design — cold-start feed for logged-out users",
    "/api/v1/chat/ws/docs": "static websocket protocol docs",
    "/api/v1/influencers": "public catalogue — mobile browses it logged-out",
    "/api/v1/influencers/trending": "public catalogue",
    "/api/v2/videogen/providers": "static provider list, no user data",
    "/api/v2/videogen/providers-all": "static provider list, no user data",
}

# Admin routes authenticate with X-Admin-Key rather than a bearer token, so 403
# is their correct anonymous answer. 503 counts as guarded because the
# amorae-secret routes (spicy handoff, nsfw-consent) fail CLOSED when Redis is
# unreachable — a stranger still gets nothing, which is the property under test.
GUARDED_STATUSES = {401, 403, 404, 405, 422, 503}


def _testable_routes(app):
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or path in PUBLIC:
            continue
        if "{" in path:  # needs a concrete id; covered by the per-router suites
            continue
        for m in sorted(methods & {"GET", "POST", "PATCH", "DELETE", "PUT"}):
            yield m, path


def _cases():
    from main import app

    return sorted(set(_testable_routes(app)))


@pytest.fixture(scope="module")
def client():
    from main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("method,path", _cases())
def test_route_refuses_an_anonymous_caller(client, method, path):
    """A 2xx here means the endpoint served a stranger."""
    response = client.request(method, path)

    assert response.status_code in GUARDED_STATUSES, (
        f"{method} {path} answered {response.status_code} to an anonymous "
        f"caller. If that is intended, add it to PUBLIC with a reason."
    )


def test_the_allowlist_only_names_routes_that_exist():
    """A stale allowlist entry silently exempts nothing and hides that a
    public route was renamed or removed."""
    from main import app

    known = {getattr(r, "path", None) for r in app.routes}
    stale = {p for p in PUBLIC if p not in known}
    assert not stale, f"PUBLIC names routes that no longer exist: {sorted(stale)}"
