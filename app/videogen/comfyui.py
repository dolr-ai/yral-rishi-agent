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
        # Reading can take as long as a generation does; connecting is one hop
        # across the overlay and must not inherit that budget.
        timeout=httpx.Timeout(
            config.COMFYUI_TIMEOUT_SECONDS,
            connect=config.COMFYUI_CONNECT_TIMEOUT_SECONDS,
        ),
    )


def _reason(e: BaseException | None) -> str:
    """A description of a failure that is never blank.

    httpx timeouts raised through anyio carry an empty message, so the obvious
    `f"submit failed: {e}"` wrote a bare `submit failed: ` into the database —
    which is exactly what the first real failure of this service looked like on
    2026-08-26, saying nothing about whether the box was down, refusing, or
    simply unreachable. Falling back to the exception's class name always
    names the failure mode.
    """
    if e is None:
        return "unknown error"
    return str(e) or type(e).__name__


async def _post(path: str, what: str, **kwargs) -> httpx.Response:
    """POST to ComfyUI, retrying a connection that never landed.

    The tunnel to the GPU box is a Swarm *global* service — one task per node,
    all six behind a single virtual IP — and the load balancer picks a task per
    connection. So a node whose encrypted-overlay path has not converged (which
    is the normal state for a few minutes after a redeploy moves a container)
    blackholes roughly one connection in six while the other five are fine.
    That is what lost a user's video on 2026-08-26.

    Each attempt opens a *new* client, and therefore a new connection, which is
    what makes the retry worth anything: it is routed afresh and lands on a
    different tunnel. Retrying is only correct for transport failures — a reply
    from ComfyUI itself, however unwelcome, would be identical from every task.
    """
    last: Exception | None = None
    for attempt in range(1, config.COMFYUI_ATTEMPTS + 1):
        async with _client() as client:
            try:
                resp = await client.post(path, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                raise ComfyUnavailable(
                    f"{what} refused: HTTP {e.response.status_code}"
                ) from e
            except httpx.HTTPError as e:
                last = e
                logger.warning(
                    "videogen: %s attempt %d/%d failed: %s",
                    what,
                    attempt,
                    config.COMFYUI_ATTEMPTS,
                    _reason(e),
                )
    raise ComfyUnavailable(
        f"{what} failed after {config.COMFYUI_ATTEMPTS} attempts: {_reason(last)}"
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
    resp = await _post(
        "/upload/image",
        "image upload",
        files={"image": (filename, image_bytes, "application/octet-stream")},
        data={"overwrite": "true"},
    )
    stored = resp.json()
    name = stored.get("name") or filename
    subfolder = stored.get("subfolder") or ""
    return f"{subfolder}/{name}" if subfolder else name


async def submit(graph: dict) -> str:
    """Queue the graph. Returns ComfyUI's prompt id."""
    resp = await _post("/prompt", "submit", json={"prompt": graph})
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
            logger.warning(
                "videogen: history poll failed for %s: %s", prompt_id, _reason(e)
            )
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
