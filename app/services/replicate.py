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

    When `lora_weights_url` is set, routes through the flux-dev LoRA
    path for face/body consistency (design §2). Absent = fallback to
    `nano-banana-pro` (fine-tune-less, useful for CI + first-boot
    without a trained LoRA yet)."""
    if not config.REPLICATE_API_TOKEN:
        return []
    if lora_weights_url:
        model = "black-forest-labs/flux-dev"
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
        input_data = {"prompt": prompt}

    tasks = [_run_prediction(model, input_data) for _ in range(n)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    urls: list[str] = []
    for r in results:
        if isinstance(r, str) and r:
            urls.append(r)
    return urls


async def _run_prediction(model: str, input_data: dict) -> str | None:
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
