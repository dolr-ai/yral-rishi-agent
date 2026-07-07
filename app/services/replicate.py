import asyncio
import logging

import httpx

import config

logger = logging.getLogger(__name__)


async def generate_image(prompt: str, aspect_ratio: str = "1:1") -> str | None:
    if not config.REPLICATE_API_TOKEN:
        return None
    return await _run_prediction(
        model=config.REPLICATE_MODEL,
        input_data={
            "prompt": prompt,
            "go_fast": True,
            "megapixels": "1",
            "aspect_ratio": aspect_ratio,
            "output_format": "jpg",
            "output_quality": 80,
        },
    )


async def generate_image_with_reference(
    prompt: str,
    reference_image_url: str,
    aspect_ratio: str = "9:16",
) -> str | None:
    if not config.REPLICATE_API_TOKEN:
        return None
    return await _run_prediction(
        model="black-forest-labs/flux-kontext-dev",
        input_data={
            "prompt": prompt,
            "go_fast": True,
            "guidance": 2.5,
            "megapixels": "1",
            "num_inference_steps": 30,
            "aspect_ratio": aspect_ratio,
            "output_format": "jpg",
            "output_quality": 80,
            "input_image": reference_image_url,
        },
    )


async def generate_batch(
    prompt: str,
    n: int,
    lora_weights_url: str | None = None,
) -> list[str]:
    """Phase 0 Request Images track B — fire N image generations in
    parallel for a single collage. Returns the successful URLs; a
    caller comparing len(result) < n knows the batch was partially
    blocked (Replicate safety refusal manifests as None → filtered
    here). image_collage.orchestrate uses that shortfall as the
    "batch failed content safety" signal per design §2.5.

    Routing (Rishi choice 2026-07-07 — Option C hybrid):
      1. LoRA model ref + COLLAGE_HYBRID_MODE=true (default): generate
         ONE anchor image via LoRA versioned endpoint (identity lock)
         then N nano-banana-pro calls with the anchor as `image_input`
         (best-model scene quality + identity durability).
      2. LoRA model ref + hybrid off: fall back to N × flux-dev-LoRA
         (pre-hybrid behavior, retained as a hot-editable escape).
      3. LoRA URL (HF/CivitAI/safetensors): N × flux-dev + lora_weights.
      4. No LoRA: N × nano-banana-pro (pre-LoRA fallback for bots
         without a trained LoRA yet)."""
    if not config.REPLICATE_API_TOKEN:
        return []
    if lora_weights_url:
        if lora_weights_url.startswith(("http://", "https://")):
            _lora_is_model_ref = False
        else:
            _lora_is_model_ref = "/" in lora_weights_url

        if _lora_is_model_ref and config.COLLAGE_HYBRID_MODE:
            # OPTION C HYBRID (Rishi choice 2026-07-07, superseding the
            # 2026-07-06 pure-LoRA smoke-test outcome that first proved
            # identity durability): LoRA anchor + nano-banana-pro
            # batch. The LoRA (ostris-trained, e.g. `yral/tara-lora-v1:V`)
            # guarantees identity — it "knows Tara". Nano-banana-pro is
            # the highest-quality general scene model but zero-shot
            # identity drifts on named characters. Solution: generate
            # ONE anchor via the LoRA with today's theme prompt, then
            # feed the anchor into nano-banana-pro N times as
            # `image_input` — nano-banana-pro then preserves the
            # anchor's identity while producing high-quality variations.
            # Verified schema 2026-07-07:
            # google/nano-banana-pro accepts up to 14 reference images
            # via `image_input: array`. Anchor is prompt-specific
            # (regenerated per batch) so it always matches today's
            # theme; caching the anchor across batches would lock us
            # to yesterday's scene shape.
            model, _, version = lora_weights_url.partition(":")
            version = version or None
            anchor_url = await _run_prediction(
                model,
                {
                    "prompt": prompt,
                    "num_inference_steps": 28,
                    "aspect_ratio": "9:16",
                    "output_format": "jpg",
                    "output_quality": 85,
                },
                version=version,
            )
            if not anchor_url:
                # Anchor generation failed → don't produce identityless
                # nano-banana-pro outputs; return empty and let
                # image_collage.orchestrate mark the batch failed
                # (design §2.5).
                logger.error(
                    "Hybrid pipeline: LoRA anchor generation returned None; "
                    "aborting batch to avoid identity-drifted outputs"
                )
                return []
            model = "google/nano-banana-pro"
            version = None
            input_data = {
                "prompt": prompt,
                "image_input": [anchor_url],
                "aspect_ratio": "9:16",
                "output_format": "jpg",
            }
        elif _lora_is_model_ref:
            # Pure-LoRA fallback (COLLAGE_HYBRID_MODE=false). Same as
            # the pre-hybrid behavior — N × flux-dev-LoRA via the
            # versioned endpoint. Kept as a hot-editable escape lever
            # in case hybrid regresses.
            model, _, version = lora_weights_url.partition(":")
            version = version or None
            input_data = {
                "prompt": prompt,
                "num_inference_steps": 28,
                "aspect_ratio": "9:16",
                "output_format": "jpg",
                "output_quality": 85,
            }
        else:
            # HF/CivitAI/safetensors URL — pass to flux-dev's
            # lora_weights param (documented pattern at
            # https://replicate.com/black-forest-labs/flux-dev).
            model = "black-forest-labs/flux-dev"
            version = None
            input_data = {
                "prompt": prompt,
                "lora_weights": lora_weights_url,
                "megapixels": "1",
                "num_inference_steps": 28,
                "output_format": "jpg",
                "output_quality": 85,
            }
    else:
        model = "google/nano-banana-pro"
        version = None
        input_data = {"prompt": prompt}

    tasks = [_run_prediction(model, input_data, version=version) for _ in range(n)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    urls: list[str] = []
    for r in results:
        if isinstance(r, str) and r:
            urls.append(r)
    return urls


async def _run_prediction(
    model: str, input_data: dict, version: str | None = None
) -> str | None:
    # Custom trained (user-owned) models require the versioned endpoint.
    # Official Replicate models get the shorthand /v1/models/{model}/predictions
    # form, which auto-picks their latest published version.
    if version:
        url = (
            f"https://api.replicate.com/v1/models/{model}"
            f"/versions/{version}/predictions"
        )
    else:
        url = f"https://api.replicate.com/v1/models/{model}/predictions"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                json={"input": input_data},
                headers={
                    "Authorization": f"Bearer {config.REPLICATE_API_TOKEN}",
                    "Prefer": "wait",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code >= 400:
                logger.error(
                    f"Replicate API error: {response.status_code} — {response.text}"
                )
                return None

            data = response.json()
            status = data.get("status", "")

            if status == "succeeded":
                return _extract_output_url(data.get("output"))
            if status in ("starting", "processing"):
                poll_url = (
                    data.get("urls", {}).get("get")
                    or f"https://api.replicate.com/v1/predictions/{data['id']}"
                )
                return await _poll_prediction(client, poll_url)
            return None
    except Exception as e:
        logger.error(f"Replicate image generation failed: {e}")
        return None


async def _poll_prediction(client: httpx.AsyncClient, url: str) -> str | None:
    for _ in range(30):
        await asyncio.sleep(2)
        try:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {config.REPLICATE_API_TOKEN}"},
                timeout=10,
            )
            data = response.json()
            status = data.get("status", "")
            if status == "succeeded":
                return _extract_output_url(data.get("output"))
            elif status in ("failed", "canceled"):
                return None
        except Exception as e:
            logger.warning(f"Replicate poll error: {e}")
            continue
    return None


def _extract_output_url(output) -> str | None:
    if isinstance(output, list) and output:
        return str(output[0])
    elif isinstance(output, str):
        return output
    return None
