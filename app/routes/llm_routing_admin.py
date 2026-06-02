"""Phase 25.4 — admin hot-swap endpoint for LLM routing.

PATCH /admin/llm-routing — change which provider/model serves a process,
no redeploy required. JWT-gated through the canonical /admin/* auth
flow.

Per docs/PHASE-25-DESIGN.md and the ADHD-friendly editability rule:
every cap/mapping/limit must be two-click editable on the dashboard.
This endpoint is the API the 19.6/25.9 dashboard tile calls.

Persistence: llm_process_config table (migration 026). On a successful
PATCH, the registry's in-memory cache reloads — change takes effect on
the next call, no restart needed.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from auth import get_current_user
from database import get_pool
from services import llm_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — LLM routing"])


def _check_admin_auth(request: Request) -> str:
    """Same auth shape as admin_dashboard — header OR ?token=. Returns
    the principal ID for the audit-trail (updated_by column)."""
    if request.headers.get("Authorization"):
        return get_current_user(request)
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Provide a JWT either as 'Authorization: Bearer <jwt>' header OR as ?token=<jwt> query param.",
        )
    # Re-route through canonical validator
    fake = type("R", (), {"headers": {"Authorization": f"Bearer {token}"}})()
    return get_current_user(fake)


@router.get("/admin/llm-routing")
async def list_routing(request: Request):
    """Show every process's currently-resolved (provider, model, ...).
    Used by the 19.6 dashboard tile. JWT-gated."""
    _check_admin_auth(request)
    return {
        "processes": [
            llm_registry.current_config(p) for p in llm_registry.PROCESS_NAMES
        ],
        "providers": {
            name: {
                "concurrency_cap": meta.get("concurrency_cap"),
                "cost_basis": meta.get("cost_basis"),
                "supports_chat": meta.get("supports_chat", False),
                "supports_stream": meta.get("supports_stream", False),
                "supports_transcribe": meta.get("supports_transcribe", False),
            }
            for name, meta in llm_registry.PROVIDERS.items()
        },
    }


@router.patch("/admin/llm-routing/{process}")
async def patch_routing(process: str, body: dict, request: Request):
    """Hot-edit one process's provider/model. Body shape:
        {"provider": "gemini", "model": "gemini-2.5-flash", "timeout_sec": 60}
    timeout_sec is optional; omit to inherit the LLM_DEFAULTS value.

    Capability check: if the new provider doesn't support the modality
    that process needs (e.g. switching audio_transcription to a provider
    without supports_transcribe), we reject with 400 so the operator
    sees the mismatch immediately."""
    principal = _check_admin_auth(request)

    provider = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()
    timeout_sec = body.get("timeout_sec")

    if not provider:
        raise HTTPException(status_code=400, detail="'provider' is required")
    if not model:
        raise HTTPException(status_code=400, detail="'model' is required")
    if process not in llm_registry.PROCESS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown process '{process}'. Known: {sorted(llm_registry.PROCESS_NAMES)}",
        )
    if provider not in llm_registry.PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider '{provider}'. Known: {sorted(llm_registry.PROVIDERS.keys())}",
        )

    # Capability check — refuse to break the modality contract.
    provider_meta = llm_registry.PROVIDERS[provider]
    if process == "audio_transcription" and not provider_meta.get(
        "supports_transcribe", False
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider '{provider}' does not support audio transcription. "
                f"Today only providers with supports_transcribe=True work for "
                f"the audio_transcription process."
            ),
        )

    if isinstance(timeout_sec, str):
        try:
            timeout_sec = float(timeout_sec)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="'timeout_sec' must be a number"
            ) from None

    pool = await get_pool()
    try:
        await llm_registry.upsert_override(
            pool,
            process=process,
            provider=provider,
            model=model,
            timeout_sec=timeout_sec,
            updated_by=principal,
        )
    except Exception as e:
        # Most likely cause: llm_process_config table doesn't exist yet
        # (Rule 9 — Rishi hasn't pg_dumped + applied migration 026 yet).
        logger.error("llm_registry.upsert_override failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=(
                "llm_process_config table not available yet (migration 026 "
                "pending). Hot-edits will work after Rishi applies the "
                "migration; meanwhile, use LLM_PROCESS__<NAME>=<provider>/<model> "
                "env vars."
            ),
        ) from e

    logger.info(
        "llm_routing PATCH: process=%s provider=%s model=%s by=%s",
        process,
        provider,
        model,
        principal,
    )
    return {"process": process, "current": llm_registry.current_config(process)}


@router.delete("/admin/llm-routing/{process}")
async def delete_routing(process: str, request: Request):
    """Remove an override — process falls back to env + LLM_DEFAULTS."""
    principal = _check_admin_auth(request)
    if process not in llm_registry.PROCESS_NAMES:
        raise HTTPException(status_code=400, detail=f"unknown process '{process}'")
    pool = await get_pool()
    try:
        deleted = await llm_registry.delete_override(
            pool, process=process, updated_by=principal
        )
    except Exception as e:
        logger.error("llm_registry.delete_override failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="llm_process_config table not available yet (migration 026 pending)",
        ) from e

    return {
        "process": process,
        "deleted": deleted,
        "current": llm_registry.current_config(process),
    }
