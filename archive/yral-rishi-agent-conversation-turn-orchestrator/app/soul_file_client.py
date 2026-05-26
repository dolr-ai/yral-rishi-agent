# ---------------------------------------------------------------------------
# soul_file_client.py — HTTP client to the soul-file-library service.
#
# ⭐ START HERE: this module exposes:
#   1. `init_soul_file_client()` / `close_soul_file_client()` — async
#      lifecycle pair the FastAPI lifespan calls.
#   2. `get_soul_file_client()` — singleton accessor for the rest of
#      the codebase (today: only `run_turn.py`).
#   3. `SoulFileClient.compose(influencer_id, user_segment)` — async
#      RPC call that returns the 4-layer composed prompt per the
#      contract at `interface-contracts/01-internal-rpc-contracts.md`
#      "orchestrator → soul-file-library" section.
#   4. `SoulFileInfluencerNotFoundError` + `SoulFileUpstreamError` —
#      typed exceptions `run_turn.py` catches + maps to 404 / 503
#      envelope responses.
#
# WHAT THE RPC LOOKS LIKE
# Per the locked contract (PR #98 verbatim):
#     GET http://yral-rishi-agent-soul-file-library:8000/composed-prompt
#       ?influencer_id=<uuid>&user_segment=<new|paying|dormant>
#     →  200 { layered_prompt, version_pin, cache_hit }
#     →  404 { ApiResponse envelope } when influencer_id has no L3 row
#     →  422 { ... }  when user_segment isn't in the enum
#     →  500 { ApiResponse envelope } on data-integrity issues
#
# WHY THE LIFESPAN-MANAGED httpx.AsyncClient SINGLETON
# Per A2.1 (minimal) + the Day-4C public-api pattern: one client per
# service, opened at startup, closed at shutdown. Re-using the client
# means we re-use the connection pool — Day-5 hot path latency wins
# (E1) come from avoiding TCP handshake on every turn.
#
# WHY THE TWO TYPED EXCEPTIONS
# Same shape as `llm_client.base`: split "they're broken" from "they
# said no" so `run_turn.py` can produce distinct envelopes. The 404
# envelope (`influencer_not_found`) is a client-side correction
# surface (caller misconfigured `ai_influencer_id`); the 503 envelope
# (`soul_file_upstream_unavailable`) is an ops-side signal.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `dataclass(frozen=True)` builds the immutable typed return
# shape. Three fields match the contract verbatim.
from dataclasses import dataclass

# stdlib logger — emits structured fields the H6 allowlist redactor
# in `app/logging.py` knows about. NEVER logs the layered_prompt
# content (that's the soul-file body; treated as a secret).
import logging

# `Final` declares module-level constants the type-checker treats as
# immutable. Locked URL path + timeout values surface in `git blame`.
from typing import Final

# `httpx` — async HTTP client matching the rest of the v2 stack
# (per pyproject.toml + the new-service template's outbound RPC
# pattern). The same library powers Day-4C public-api's orchestrator
# calls; reusing it here keeps the dep tree thin.
import httpx

# `get_settings()` reads the singleton Settings — needs the
# soul-file-library base URL (declared in Settings + sourced from
# shared-config.yaml in deploy environments per C7).
from app.config import get_settings


# Module-level singleton. Populated by `init_soul_file_client()` at
# app startup; None before init / after close so any out-of-lifecycle
# access fails fast with a clear error rather than silently using a
# stale handle.
_client: "SoulFileClient | None" = None


# RPC path. Locked in `interface-contracts/01-internal-rpc-contracts.md`;
# a contract change requires a coordinator-owned PR there + this
# constant to move together. STAYS a constant because it's a contract
# identifier (not a tunable) — per C7 the rule is "no hardcoded
# values"; a wire-protocol path is closer to a wire-format identifier
# than a tunable knob.
_COMPOSED_PROMPT_PATH: Final[str] = "/composed-prompt"

# Note: the RPC call timeout used to be a hardcoded `_SOUL_FILE_CALL_
# TIMEOUT_SECONDS = 5.0` here. Per C7 (Codex PR-#109 round-2 BLOCKER 2)
# it now lives on Settings as `soul_file_call_timeout_seconds` and
# flows through the `SoulFileClient(..., call_timeout_seconds=...)`
# constructor + the lifespan init below.


_log = logging.getLogger("app.soul_file_client")


@dataclass(frozen=True)
class ComposedPrompt:
    """Typed return shape for the soul-file-library RPC.

    WHAT: three-field immutable record matching the contract verbatim.
    WHEN: returned by `SoulFileClient.compose(...)` on a 200 response.
    WHY:  one typed shape so `run_turn.py` doesn't unpack a dict
          (typo-prone). A future contract bump that adds a fourth
          field edits this dataclass in one place.

    Fields:
      layered_prompt — the 4-layer composed prompt (L1 global +
                       L2 archetype + L3 per-influencer + L4 per-
                       user-segment). Passed straight to
                       `LlmClient.generate(prompt=...)`.
      version_pin    — 16-char hex digest of the layer rows used.
                       For rollback diagnostics; today's caller doesn't
                       inspect it but operators can grep for it in
                       Langfuse traces.
      cache_hit      — True when the soul-file-library served the
                       prompt from its (Day-5+) Redis cache. Today's
                       Day-4 build returns False always.
    """

    layered_prompt: str
    version_pin: str
    cache_hit: bool


class SoulFileInfluencerNotFoundError(Exception):
    """The soul-file-library returned 404 for the given influencer_id.

    WHAT: typed exception raised when the RPC returns 404 (no L3 row
          for the influencer).
    WHEN: raised inside `SoulFileClient.compose(...)` on a 404
          upstream response.
    WHY:  caller-side mis-config surface — distinguishable from
          "upstream broke" (`SoulFileUpstreamError`) so `run_turn.py`
          can return a 404 envelope (`influencer_not_found`) instead
          of a 503 (`soul_file_upstream_unavailable`).
    """

    pass


class SoulFileUpstreamError(Exception):
    """The soul-file-library is unreachable or returned a 5xx / unexpected shape.

    WHAT: typed exception raised when the RPC times out, fails network-
          level, returns a 5xx status, or returns a body that doesn't
          parse against the contract shape.
    WHEN: raised inside `SoulFileClient.compose(...)` on every non-200,
          non-404 path.
    WHY:  one exception class covers the whole "they're broken" surface.
          Operator-side detail goes to Sentry + Langfuse + the
          structured log; the caller maps to a single 503 envelope.
    """

    pass


class SoulFileClient:
    """HTTP client wrapper for the soul-file-library RPC.

    WHAT: holds a lifespan-managed `httpx.AsyncClient` pointed at the
          soul-file-library base URL + exposes one async `.compose(...)`
          method.
    WHEN: instantiated once by `init_soul_file_client()` at lifespan
          startup; same instance serves every concurrent request.
    WHY:  one client per service keeps the connection pool warm so
          Day-5+ chat-turn latency (E1) doesn't pay TCP-handshake
          cost on every turn.
    """

    def __init__(self, *, base_url: str, call_timeout_seconds: float) -> None:
        """Build the underlying httpx.AsyncClient with sane defaults.

        WHAT: constructs the AsyncClient with the base URL + a
              connect-and-read timeout matching the RPC's expected
              latency budget.
        WHEN: called from `init_soul_file_client()`.
        WHY:  one place to centralise the client config; callers
              only see `.compose(...)`. Per C7 (Codex PR-#109 round-2
              BLOCKER 2) timeout is an explicit constructor kwarg
              sourced from Settings rather than a hardcoded constant.

        Args:
          base_url             — soul-file-library base URL.
                                 settings.soul_file_library_base_url is
                                 the lifespan source.
          call_timeout_seconds — per-call HTTP timeout.
                                 settings.soul_file_call_timeout_seconds
                                 is the lifespan source.
        """
        if not base_url:
            raise ValueError(
                "SoulFileClient requires a non-empty base_url. Source it "
                "from settings.soul_file_library_base_url (defaults to the "
                "Docker DNS name for the sibling service)."
            )
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=call_timeout_seconds,
        )
        self._call_timeout_seconds: Final[float] = call_timeout_seconds
        _log.info(
            "soul_file_client_initialised",
            extra={
                "base_url": base_url,
                "call_timeout_seconds": call_timeout_seconds,
            },
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client + free its connection pool.

        WHAT: awaits `self._http.aclose()`.
        WHEN: called from `close_soul_file_client()` at lifespan shutdown.
        WHY:  un-closed httpx clients can leak file descriptors during
              long-running CI matrices; clean shutdown is cheap.
        """
        await self._http.aclose()
        _log.info("soul_file_client_closed")

    async def compose(
        self,
        *,
        influencer_id: str,
        user_segment: str,
    ) -> ComposedPrompt:
        """Call GET /composed-prompt + return the typed ComposedPrompt.

        WHAT: issues the GET with `influencer_id` + `user_segment` as
              query params; parses the 200 body into ComposedPrompt;
              maps non-200 responses to typed exceptions.
        WHEN: called once per chat turn from `run_turn.py` BEFORE the
              LLM call.
        WHY:  one method per RPC verb; same shape every consumer uses
              regardless of provider behind the soul-file-library.

        Args:
          influencer_id — UUID-shaped string identifying the AI Influencer
                          whose Layer-3 soul-file row to fetch.
          user_segment  — one of "new" / "paying" / "dormant". Selects
                          the Layer-4 personalisation row.

        Returns:
          ComposedPrompt with the three contract fields populated.

        Raises:
          SoulFileInfluencerNotFoundError — upstream returned 404.
          SoulFileUpstreamError           — every other failure path
                                            (timeout, network, 5xx,
                                            422, unparseable body).
        """
        # Query-param dict — httpx URL-encodes values automatically;
        # nothing here needs special handling.
        query_params = {
            "influencer_id": influencer_id,
            "user_segment": user_segment,
        }

        try:
            response = await self._http.get(
                _COMPOSED_PROMPT_PATH,
                params=query_params,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as transport_error:
            # Connection failed or timed out. Map to the typed upstream
            # exception `run_turn.py` catches.
            _log.warning(
                "soul_file_rpc_transport_error",
                extra={
                    "influencer_id": influencer_id,
                    "user_segment": user_segment,
                    "error_type": type(transport_error).__name__,
                },
            )
            raise SoulFileUpstreamError(
                f"soul-file-library unreachable: {type(transport_error).__name__}"
            ) from transport_error

        # 404 = caller-side mis-config (unknown influencer). Distinguish
        # from upstream-broke so run_turn can emit a different envelope.
        if response.status_code == 404:
            _log.info(
                "soul_file_rpc_influencer_not_found",
                extra={
                    "influencer_id": influencer_id,
                    "user_segment": user_segment,
                },
            )
            raise SoulFileInfluencerNotFoundError(
                f"soul-file-library has no L3 row for influencer_id={influencer_id!r}"
            )

        # Every other non-200 is "they're broken" from our perspective.
        if response.status_code != 200:
            _log.warning(
                "soul_file_rpc_unexpected_status",
                extra={
                    "influencer_id": influencer_id,
                    "user_segment": user_segment,
                    "status_code": response.status_code,
                },
            )
            raise SoulFileUpstreamError(
                f"soul-file-library returned unexpected status_code={response.status_code}"
            )

        # 200 path — parse the body. Defensive: if the contract shape
        # is missing a field, treat as upstream error rather than crash
        # the orchestrator with a KeyError.
        try:
            body = response.json()
            return ComposedPrompt(
                layered_prompt=body["layered_prompt"],
                version_pin=body["version_pin"],
                cache_hit=bool(body["cache_hit"]),
            )
        except (KeyError, ValueError, TypeError) as parse_error:
            _log.error(
                "soul_file_rpc_unparseable_body",
                extra={
                    "influencer_id": influencer_id,
                    "user_segment": user_segment,
                    "error_type": type(parse_error).__name__,
                },
            )
            raise SoulFileUpstreamError(
                f"soul-file-library returned unparseable body: {type(parse_error).__name__}"
            ) from parse_error


async def init_soul_file_client() -> None:
    """Build the module-level SoulFileClient at lifespan startup.

    WHAT: constructs SoulFileClient from settings.soul_file_library_base_url
          + assigns to `_client`. Idempotent — no-op if already set
          (helpful for tests that inject a fake via monkeypatch).
    WHEN: called from `app/main.py`'s lifespan startup hook.
    WHY:  central init means every callsite sees the same pooled
          client + close happens cleanly on SIGTERM.
    """
    global _client

    if _client is not None:
        _log.debug(
            "init_soul_file_client called but client already initialised; skipping"
        )
        return

    settings = get_settings()
    _client = SoulFileClient(
        base_url=settings.soul_file_library_base_url,
        call_timeout_seconds=settings.soul_file_call_timeout_seconds,
    )


async def close_soul_file_client() -> None:
    """Close the module-level SoulFileClient at lifespan shutdown.

    WHAT: awaits `_client.aclose()` + sets `_client = None`.
    WHEN: called from `app/main.py`'s lifespan shutdown hook.
    WHY:  clean shutdown == no leaked sockets after SIGTERM.
    """
    global _client

    if _client is None:
        return

    await _client.aclose()
    _client = None


def get_soul_file_client() -> SoulFileClient:
    """Return the initialised SoulFileClient singleton.

    WHAT: hands out the module-level `_client`. Raises if init hasn't
          run.
    WHEN: called from `run_turn.py` per chat turn.
    WHY:  central accessor means a future swap (e.g. routing via a
          service discovery layer) only edits init + this getter.
    """
    if _client is None:
        raise RuntimeError(
            "soul_file_client is not initialised — call `init_soul_file_client()` "
            "in the FastAPI lifespan startup hook before any request handler."
        )
    return _client


# ===========================================================================
# RELATED FILES:
#   main.py        — calls init_soul_file_client() + close_soul_file_client()
#                    inside the lifespan
#   run_turn.py    — consumer; calls get_soul_file_client().compose(...)
#                    + catches the two typed exceptions
#   config.py      — Settings.soul_file_library_base_url
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                  — locked "orchestrator → soul-file-library" contract
#                    this file implements
#   ../tests/test_soul_file_client.py
#                  — mocked httpx tests covering happy + 404 + 503 paths
# ===========================================================================
