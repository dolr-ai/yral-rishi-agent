"""ComfyUI client — submit a graph, poll for it, fetch the result.

ComfyUI has its own queue (`POST /prompt` returns a prompt id, `GET /history/{id}`
returns the outputs once it finishes), which is why this service needs no message
broker of its own. The previous design put RabbitMQ and a bespoke Rust worker in
front of exactly these three calls.

The graph itself lives in `workflows/ltx2.json` rather than in code, because
ComfyUI exports that JSON directly from its UI — when the models on the GPU box
change, someone re-exports and drops the file in. Only four values are injected.
"""

import json
import logging
import pathlib
import random

import httpx

import config

logger = logging.getLogger(__name__)

_WORKFLOW_PATH = pathlib.Path(__file__).parent / "workflows" / "ltx2.json"

# Node ids inside ltx2.json. They come from the exported graph, so they are
# opaque strings rather than anything we chose.
NODE_IS_TEXT_TO_VIDEO = "267:201"  # PrimitiveBoolean — bypasses the image path
NODE_DURATION = "267:225"
NODE_PROMPT = "267:266"
NODE_IMAGE = "267:276"  # LoadImage — references a filename ComfyUI already holds
NODE_SEED_PASS1 = "267:237"
NODE_SEED_PASS2 = "267:216"

MAX_DURATION_SECONDS = 15


class ComfyUnavailable(RuntimeError):
    """ComfyUI could not be reached or refused the job."""


def _client() -> httpx.AsyncClient:
    headers = {}
    if config.COMFYUI_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {config.COMFYUI_AUTH_TOKEN}"
    return httpx.AsyncClient(
        base_url=config.COMFYUI_BASE_URL,
        headers=headers,
        timeout=config.COMFYUI_TIMEOUT_SECONDS,
    )


def build_workflow(
    *,
    prompt: str,
    duration_seconds: int | None,
    image_filename: str | None,
) -> dict:
    """Load the exported graph and inject this request's values.

    One graph serves both modes: `NODE_IS_TEXT_TO_VIDEO` feeds the `bypass`
    input of the two image-conditioning nodes, so text-to-video runs the same
    nodes with the image path switched off.

    Both sampler seeds are randomised per request — the exported graph has them
    fixed at 0, which would make every generation of the same prompt identical.
    """
    graph = json.loads(_WORKFLOW_PATH.read_text())

    is_t2v = image_filename is None
    graph[NODE_IS_TEXT_TO_VIDEO]["inputs"]["value"] = is_t2v
    graph[NODE_PROMPT]["inputs"]["value"] = prompt
    graph[NODE_DURATION]["inputs"]["value"] = min(
        duration_seconds or MAX_DURATION_SECONDS, MAX_DURATION_SECONDS
    )
    if image_filename is not None:
        graph[NODE_IMAGE]["inputs"]["image"] = image_filename
    for node in (NODE_SEED_PASS1, NODE_SEED_PASS2):
        graph[node]["inputs"]["noise_seed"] = random.randint(0, 2**32 - 1)
    return graph


async def upload_image(image_bytes: bytes, filename: str) -> str:
    """Hand ComfyUI the source image for image-to-video and return the filename
    its `LoadImage` node should reference.

    The old pipeline staged this to object storage, passed a URL to the worker,
    and had the worker download and re-upload it here. It goes straight in.
    """
    async with _client() as client:
        try:
            resp = await client.post(
                "/upload/image",
                files={"image": (filename, image_bytes, "application/octet-stream")},
                data={"overwrite": "true"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ComfyUnavailable(f"image upload failed: {e}") from e
    stored = resp.json()
    name = stored.get("name") or filename
    subfolder = stored.get("subfolder") or ""
    return f"{subfolder}/{name}" if subfolder else name


async def submit(graph: dict) -> str:
    """Queue the graph. Returns ComfyUI's prompt id."""
    async with _client() as client:
        try:
            resp = await client.post("/prompt", json={"prompt": graph})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ComfyUnavailable(f"submit failed: {e}") from e
    prompt_id = resp.json().get("prompt_id")
    if not prompt_id:
        raise ComfyUnavailable("submit returned no prompt_id")
    return prompt_id


async def poll(prompt_id: str) -> tuple[str, dict | None]:
    """Ask whether a job has finished.

    Returns `("pending", None)` while queued or running, `("done", file_ref)`
    with the saved video's `{filename, subfolder, type}`, or `("failed", None)`
    if ComfyUI recorded an error. An unreachable ComfyUI is reported as pending,
    not failed — a GPU box restart should not destroy in-flight generations; the
    stale sweep is what eventually gives up.
    """
    async with _client() as client:
        try:
            resp = await client.get(f"/history/{prompt_id}")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("videogen: history poll failed for %s: %s", prompt_id, e)
            return "pending", None

    entry = resp.json().get(prompt_id)
    if not entry:
        return "pending", None  # still queued — history only lists finished jobs

    status = (entry.get("status") or {}).get("status_str", "")
    if status == "error":
        return "failed", None

    for node_output in (entry.get("outputs") or {}).values():
        for key in ("videos", "gifs", "images"):
            files = node_output.get(key) or []
            if files:
                return "done", files[0]

    # Present in history, no error, but nothing saved — nothing to wait for.
    return "failed", None


async def fetch_output(file_ref: dict) -> bytes:
    """Download a finished video from ComfyUI.

    The video transits this service rather than being pushed straight to a
    bucket by the GPU box. That is what removes the pre-signed upload URL, its
    expiry window, and the refresh endpoint the old design needed — at the cost
    of a few MB through the app, which at this volume is nothing.
    """
    params = {
        "filename": file_ref.get("filename", ""),
        "subfolder": file_ref.get("subfolder", ""),
        "type": file_ref.get("type", "output"),
    }
    async with _client() as client:
        try:
            resp = await client.get("/view", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ComfyUnavailable(f"fetch failed: {e}") from e
    return resp.content
