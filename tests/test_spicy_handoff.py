"""Track 2a — spicy handoff mint (JWT) + exchange (X-Amorae-Secret).

Contract §1. Design §4.7. What we pin:

  - Route paths (mint = /api/v1/spicy/handoff, exchange = .../exchange).
  - Auth split (mint = JWT via get_current_user; exchange = amorae
    secret via require_amorae_secret). Same rationale as track 1b:
    silent cross-mounting is a security bug (anyone with the shared
    secret could mint tickets for arbitrary users if we swapped auth).
  - Router mounted in main.

Service behavior:
  - mint generates high-entropy url-safe tickets, SETEX with 60s TTL.
  - exchange uses atomic GETDEL — the second exchange of the same
    ticket MUST return None (single-use enforcement).
  - exchange returns None on unknown / expired / malformed payload
    (never raises — the caller maps None to 401).
  - Redis unavailable at mint time raises RuntimeError (route
    surfaces 503, does NOT return a garbage ticket).

Behavioural tests use a small in-memory Redis stub so we can pin
GETDEL semantics + SETEX TTL wiring without needing a live Redis.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

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


# ─── source-pin ─────────────────────────────────────────────────────────


def test_route_prefix_locked():
    """Contract §1 pins /api/v1/spicy/handoff for both mint + exchange
    (exchange is /api/v1/spicy/handoff/exchange)."""
    src = _read("app/routes/spicy_handoff.py")
    assert 'APIRouter(prefix="/api/v1/spicy/handoff"' in src
    # Mint is the root of the prefix (empty path string); exchange is
    # a sub-path. Anchor on `@router.post("",` and `"/exchange"` — the
    # ruff formatter may line-break either decorator so avoid rigid
    # single-line assertions.
    assert '@router.post("",' in src, (
        'mint route must decorate with @router.post("", ...) — anything '
        "else would move the endpoint off the /api/v1/spicy/handoff root"
    )
    assert '"/exchange"' in src, "exchange route missing the /exchange sub-path"


def test_mint_uses_jwt_exchange_uses_amorae_secret():
    """Silent cross-mounting is a security bug. If exchange became
    JWT-only, amorae couldn't call it. If mint became amorae-secret,
    anyone holding the shared secret could mint tickets for arbitrary
    users. Both mistakes fail CI here."""
    src = _read("app/routes/spicy_handoff.py")

    # Mint block: JWT (get_current_user) present, amorae dependency NOT present
    mint_pos = src.find('@router.post("",')
    assert mint_pos != -1
    # Find the NEXT @router.post( that isn't the mint's own decorator
    # opening — the exchange decorator starts a new block.
    next_route = src.find('"/exchange"', mint_pos + 10)
    mint_block = src[mint_pos : next_route if next_route != -1 else len(src)]
    assert "get_current_user(" in mint_block
    assert "require_amorae_secret" not in mint_block, (
        "mint must NOT be gated on amorae secret (JWT-only)"
    )

    # Exchange block: amorae dependency present, get_current_user NOT
    exchange_pos = src.find('"/exchange"')
    assert exchange_pos != -1
    exchange_block = src[exchange_pos:]
    assert "Depends(require_amorae_secret)" in exchange_block
    assert "get_current_user(" not in exchange_block, (
        "exchange must NOT gate on JWT (amorae is server-to-server)"
    )


def test_router_wired_in_main():
    src = _read("app/main.py")
    assert "from routes.spicy_handoff import router as spicy_handoff_router" in src
    assert "app.include_router(spicy_handoff_router)" in src


def test_ticket_ttl_matches_contract():
    """Contract §1 + design §4.7 lock the ~60s TTL. A longer window
    widens the leak-a-URL blast radius; a shorter one races the
    browser hop and would 401 legitimate users."""
    from services.spicy_handoff import TICKET_TTL_SEC

    assert TICKET_TTL_SEC == 60


def test_mint_uses_setex_not_plain_set():
    """SETEX puts the TTL on the write in one round-trip. A plain SET
    would leave the ticket alive forever (Redis has no default TTL) —
    that would silently keep tickets valid for hours if the SETEX line
    was ever refactored to SET."""
    src = _read("app/services/spicy_handoff.py")
    assert "redis.setex(" in src


def test_exchange_uses_atomic_getdel():
    """Single-use enforcement lives ENTIRELY in the GETDEL atomicity.
    A GET+DEL pair would race and let two concurrent exchanges each
    return the payload. Pin the atomic op."""
    src = _read("app/services/spicy_handoff.py")
    assert "redis.getdel(" in src, (
        "single-use requires atomic GETDEL; separate GET + DEL races"
    )


# ─── behavioural — service ──────────────────────────────────────────────


class _StubRedis:
    """Minimal in-memory Redis stub covering setex + getdel semantics.
    Not a full Redis; just enough to exercise the handoff lifecycle."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.raise_on_next = False

    async def setex(self, key, ttl, value):
        if self.raise_on_next:
            raise RuntimeError("simulated redis error")
        self.store[key] = value
        self.ttls[key] = ttl

    async def getdel(self, key):
        if self.raise_on_next:
            raise RuntimeError("simulated redis error")
        return self.store.pop(key, None)


def _install_stub(monkeypatch, stub):
    from services import spicy_handoff

    async def _fake_get_redis():
        return stub

    monkeypatch.setattr(spicy_handoff, "_get_redis", _fake_get_redis)


@requires_fastapi
def test_mint_generates_unique_high_entropy_tickets(monkeypatch):
    from services import spicy_handoff

    stub = _StubRedis()
    _install_stub(monkeypatch, stub)

    async def run():
        t1 = await spicy_handoff.mint(user_id="u-1")
        t2 = await spicy_handoff.mint(user_id="u-1")
        return t1, t2

    t1, t2 = asyncio.run(run())
    # Never collide across mints for the same user.
    assert t1 != t2
    # url-safe base64 of 32 bytes is ≥43 chars — high-entropy sanity check.
    assert len(t1) >= 40
    # Both got stored with the 60s TTL.
    assert stub.ttls["spicy:handoff:" + t1] == 60
    assert stub.ttls["spicy:handoff:" + t2] == 60


@requires_fastapi
def test_mint_stores_full_identity_payload(monkeypatch):
    from services import spicy_handoff

    stub = _StubRedis()
    _install_stub(monkeypatch, stub)

    async def run():
        return await spicy_handoff.mint(
            user_id="u-abc",
            bot_handle="taaarraaah",
            is_anonymous=False,
        )

    ticket = asyncio.run(run())
    payload = json.loads(stub.store["spicy:handoff:" + ticket])
    assert payload == {
        "user_id": "u-abc",
        "bot_handle": "taaarraaah",
        "is_anonymous": False,
    }


@requires_fastapi
def test_mint_raises_when_redis_unavailable(monkeypatch):
    """The route layer converts this into a 503 so the app surfaces
    a real error. Silent degrade-open would land the user on the
    brand with a ticket that will never exchange."""
    from services import spicy_handoff

    async def _no_redis():
        return None

    monkeypatch.setattr(spicy_handoff, "_get_redis", _no_redis)

    async def run():
        return await spicy_handoff.mint(user_id="u-1")

    with pytest.raises(RuntimeError):
        asyncio.run(run())


@requires_fastapi
def test_exchange_returns_payload_and_consumes_ticket(monkeypatch):
    """Happy path + single-use pin: the same ticket exchanged twice
    returns the payload on call 1 and None on call 2."""
    from services import spicy_handoff

    stub = _StubRedis()
    _install_stub(monkeypatch, stub)

    async def run():
        ticket = await spicy_handoff.mint(user_id="u-1", bot_handle="tara")
        first = await spicy_handoff.exchange(ticket)
        second = await spicy_handoff.exchange(ticket)
        return first, second

    first, second = asyncio.run(run())
    assert first == {"user_id": "u-1", "bot_handle": "tara", "is_anonymous": False}
    assert second is None, (
        "single-use is broken — the ticket exchanged twice successfully"
    )


@requires_fastapi
def test_exchange_returns_none_for_unknown_ticket(monkeypatch):
    from services import spicy_handoff

    stub = _StubRedis()
    _install_stub(monkeypatch, stub)

    async def run():
        return await spicy_handoff.exchange("never-minted-ticket")

    assert asyncio.run(run()) is None


@requires_fastapi
def test_exchange_returns_none_when_redis_errors(monkeypatch):
    """The route maps None to 401 (bounce user back to landing).
    Redis blowing up must NOT propagate as a 500 — that would leak
    infrastructure details to amorae."""
    from services import spicy_handoff

    stub = _StubRedis()
    _install_stub(monkeypatch, stub)

    async def run():
        # Prime a ticket, then fail the getdel call.
        _ = await spicy_handoff.mint(user_id="u-1")
        stub.raise_on_next = True
        return await spicy_handoff.exchange("some-ticket")

    assert asyncio.run(run()) is None


@requires_fastapi
def test_exchange_returns_none_on_malformed_payload(monkeypatch):
    """Belt-and-braces: if Redis somehow returns non-JSON, we log +
    return None. Never raise, never return a partially-parsed dict."""
    from services import spicy_handoff

    class _BadPayloadRedis:
        async def setex(self, *a, **k):
            pass

        async def getdel(self, *a, **k):
            return "not json {"

    _install_stub(monkeypatch, _BadPayloadRedis())

    async def run():
        return await spicy_handoff.exchange("whatever")

    assert asyncio.run(run()) is None
