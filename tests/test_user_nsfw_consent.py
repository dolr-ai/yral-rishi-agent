"""Track 1b — spicy chat gate consent endpoints.

Pins:

  - Auth split: POST is X-Amorae-Secret-only, GET is JWT-only.
    Cross-mounting (JWT on POST or amorae-secret on GET) would
    silently break the contract with amorae-web AND break the
    native-app cross-device memory path.

  - Route paths are contract-locked (docs/amorae-v2-contract-2026-07-01.md).

  - X-Amorae-Secret middleware:
      * fails-closed with 503 when the shared secret isn't configured
        (a v2 mis-deploy must NEVER accept anonymous writes)
      * rejects missing / wrong header with 401
      * uses constant-time comparison so a timing side-channel can't
        leak the real secret
      * accepts a matching header

  - Repo shape: upsert(user_id, ...) is idempotent; get(user_id)
    returns None for a user that never confirmed.

  - Router mounted in main.py.

Behavioural DB tests need a live pool — those run in deploy verification.
"""

import importlib
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

requires_fastapi = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE, reason="fastapi not installed (CI only)"
)


# ─── source-pin — route paths + auth split ──────────────────────────────


def test_route_file_declares_locked_path():
    """`/api/v1/users/nsfw-consent` is contract-locked to amorae. A
    path change breaks amorae-web's v2_client without any local test
    signal."""
    src = _read("app/routes/user_nsfw_consent.py")
    assert 'APIRouter(prefix="/api/v1/users/nsfw-consent"' in src


def test_post_uses_amorae_secret_get_uses_jwt():
    """The auth split IS the design — POST is server-to-server from
    amorae, GET is JWT-user for cross-device memory. Locked by the
    pre-1b clarification (Session 6 verdict). Mounting either the
    other way is a silent security bug (JWT-user shouldn't be able to
    write another user_id; amorae shouldn't be able to enumerate GETs
    without a target user_id header)."""
    src = _read("app/routes/user_nsfw_consent.py")
    # POST decorator carries the shared-secret dependency
    post_marker = "@router.post("
    post_pos = src.find(post_marker)
    assert post_pos != -1
    post_line_end = src.find("\n", post_pos + 100)
    post_line = src[post_pos:post_line_end]
    assert "Depends(require_amorae_secret)" in post_line, (
        "POST must be gated by require_amorae_secret"
    )
    # GET body reads user_id via get_current_user (JWT), not from any
    # user-controllable input
    get_marker = "@router.get("
    get_pos = src.find(get_marker)
    assert get_pos != -1
    # Between the GET decorator and the next `@router.`, we should see
    # get_current_user() being called and NO reference to
    # require_amorae_secret.
    next_route_pos = src.find("@router.", get_pos + 10)
    get_block = src[get_pos : next_route_pos if next_route_pos != -1 else len(src)]
    assert "get_current_user(" in get_block, "GET must call get_current_user"
    assert "require_amorae_secret" not in get_block, (
        "GET must NOT gate on amorae secret (contract locks it JWT-only)"
    )


def test_router_wired_in_main():
    """A route file that isn't mounted might as well not exist. Pin
    both the import + the include_router call."""
    src = _read("app/main.py")
    assert (
        "from routes.user_nsfw_consent import router as user_nsfw_consent_router" in src
    )
    assert "app.include_router(user_nsfw_consent_router)" in src


# ─── behavioural — amorae_auth middleware ───────────────────────────────


@requires_fastapi
def test_amorae_secret_fails_closed_when_unconfigured(monkeypatch, tmp_path):
    """No secret file + no env var = 503. Silently accepting writes on
    an unconfigured v2 would let anyone hit POST as amorae. Pin the
    fail-closed behavior."""
    # Point the module at a bogus secret path + clear the env fallback
    # so we hit the unconfigured branch.
    monkeypatch.delenv("V2_WEB_SHARED_SECRET", raising=False)
    import amorae_auth

    monkeypatch.setattr(amorae_auth, "_SECRET_PATH", str(tmp_path / "nope"))

    from fastapi import HTTPException

    class _Req:
        headers = {"X-Amorae-Secret": "anything"}
        client = None

    with pytest.raises(HTTPException) as ei:
        amorae_auth.require_amorae_secret(_Req())
    assert ei.value.status_code == 503


@requires_fastapi
def test_amorae_secret_rejects_missing_and_wrong(monkeypatch, tmp_path):
    """The auth model is 'header equals secret'. Missing header → 401.
    Wrong value → 401. Never 200. Never 500."""
    secret_file = tmp_path / "V2_WEB_SHARED_SECRET"
    secret_file.write_text("s3cret-fixture-not-real-prod-value")
    monkeypatch.delenv("V2_WEB_SHARED_SECRET", raising=False)
    import amorae_auth

    monkeypatch.setattr(amorae_auth, "_SECRET_PATH", str(secret_file))

    from fastapi import HTTPException

    class _NoHeader:
        headers = {}
        client = None

    class _WrongHeader:
        headers = {"X-Amorae-Secret": "definitely-wrong"}
        client = None

    for req in (_NoHeader(), _WrongHeader()):
        with pytest.raises(HTTPException) as ei:
            amorae_auth.require_amorae_secret(req)
        assert ei.value.status_code == 401


@requires_fastapi
def test_amorae_secret_accepts_matching_header(monkeypatch, tmp_path):
    """The happy path. Constant-time comparison must accept bytes-
    identical input."""
    secret_file = tmp_path / "V2_WEB_SHARED_SECRET"
    secret_file.write_text("real-shared-secret-value-here")
    monkeypatch.delenv("V2_WEB_SHARED_SECRET", raising=False)
    import amorae_auth

    monkeypatch.setattr(amorae_auth, "_SECRET_PATH", str(secret_file))

    class _OkReq:
        headers = {"X-Amorae-Secret": "real-shared-secret-value-here"}
        client = None

    # Returns None on success (dependency-only side effect).
    assert amorae_auth.require_amorae_secret(_OkReq()) is None


@requires_fastapi
def test_amorae_secret_uses_env_fallback_when_file_missing(monkeypatch, tmp_path):
    """Local dev + CI don't have /run/secrets/. Env-var fallback keeps
    the module runnable without swarm secrets present."""
    monkeypatch.setenv("V2_WEB_SHARED_SECRET", "env-fallback-value")
    import amorae_auth

    monkeypatch.setattr(amorae_auth, "_SECRET_PATH", str(tmp_path / "nope"))

    class _OkReq:
        headers = {"X-Amorae-Secret": "env-fallback-value"}
        client = None

    assert amorae_auth.require_amorae_secret(_OkReq()) is None


@requires_fastapi
def test_amorae_secret_uses_constant_time_compare():
    """A timing side-channel would let an attacker recover the real
    secret one byte at a time. `secrets.compare_digest` is the
    canonical fix. Pin its use in the source so a future refactor
    that switches to `==` fails CI."""
    src = _read("app/amorae_auth.py")
    assert "secrets.compare_digest(" in src, (
        "must use secrets.compare_digest — plain `==` is a timing "
        "side-channel on the shared secret"
    )


# ─── behavioural — repo (stubbed pool) ──────────────────────────────────


@requires_fastapi
def test_repo_upsert_passes_source_ip_as_inet(monkeypatch):
    """asyncpg needs the inet cast on the source_ip parameter — a bare
    text bind would fail at the driver layer with a "column source_ip
    is of type inet" error. Pin the SQL includes `$4::inet`."""
    src = _read("app/repositories/user_nsfw_consent_repo.py")
    assert "$4::inet" in src


@requires_fastapi
def test_repo_get_returns_none_for_unknown_user():
    """The GET endpoint contract is `{confirmed: false, expires_at: null}`
    when the user has no row. That's only possible if the repo returns
    None (not raises, not returns a default dict)."""
    import asyncio

    from repositories import user_nsfw_consent_repo

    class _NoRow:
        async def fetchrow(self, sql, *args):
            return None

    result = asyncio.run(user_nsfw_consent_repo.get(_NoRow(), "u-1"))
    assert result is None


@requires_fastapi
def test_repo_upsert_uses_on_conflict_for_idempotency():
    """The amorae contract §2 says any 2xx is success — that includes
    replay writes for the same user. Pin the ON CONFLICT clause so a
    careless refactor can't turn a repeat click into a duplicate-key
    500."""
    src = _read("app/repositories/user_nsfw_consent_repo.py")
    assert "ON CONFLICT (user_id) DO UPDATE" in src
    # Refresh updated_at on the conflict path so a stale-row alert
    # doesn't fire on a still-active user.
    assert "updated_at   = NOW()" in src


# ─── behavioural — GET response shape ───────────────────────────────────


@requires_fastapi
def test_get_response_shape_omits_audit_fields(monkeypatch):
    """GET returns {confirmed, expires_at} per the design doc — NOT
    source_ip (audit field, must not leak to the user's device)."""
    # Reload the module in case a prior test mutated its state.
    import routes.user_nsfw_consent as route_mod

    importlib.reload(route_mod)

    fields = route_mod.ConsentReadResponse.model_fields
    assert set(fields.keys()) == {"confirmed", "expires_at"}, (
        f"ConsentReadResponse must expose only confirmed + expires_at; "
        f"got {set(fields.keys())}"
    )
