"""SpacetimeDB writes — as the user, never as an admin.

Posts live in SpacetimeDB. A generated video becomes a Draft via `add_post`, and
the app's Publish button flips it with `update_post_status`.

**We act as the calling user.** SpacetimeDB derives a caller `Identity` from a
JWT's `iss` + `sub`, and the app's yral-auth id_token is already minted for this
(`ext_spacetimedb_token`), so forwarding that token makes the write happen as
its owner. The alternative was holding the shared `SPACETIMEDB_ADMIN_TOKEN` —
which can rewrite any user's username, email or subscription plan, a blast
radius wildly out of proportion to creating one post for one person.

Both reducers were admin-only; `dolr-ai/yral-bare-metal-kubernetes-cluster#190`
opens them to the post's creator, matching the gate `delete_post` already uses.
Until that merges and the module is republished, these calls return
`Unauthorized` and the generation is recorded as failed rather than silently
losing the video — the file is already durable in storage either way.
"""

import logging

import httpx

import config

logger = logging.getLogger(__name__)

# Mirrors PostStatus in the SpacetimeDB module.
STATUS_DRAFT = "Draft"
STATUS_UPLOADED = "Uploaded"


class SpacetimeError(RuntimeError):
    """A reducer call failed or was refused."""


async def _call_reducer(reducer: str, args: list, user_token: str) -> None:
    """POST one reducer call, authenticated as the token's owner."""
    url = (
        f"{config.SPACETIMEDB_URL.rstrip('/')}"
        f"/v1/database/{config.SPACETIMEDB_DB_NAME}/call/{reducer}"
    )
    async with httpx.AsyncClient(timeout=config.SPACETIMEDB_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.post(
                url,
                json=args,
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise SpacetimeError(f"{reducer}: {e}") from e

    if resp.status_code >= 400:
        # Never echo the body wholesale — a reducer error can quote its input.
        raise SpacetimeError(f"{reducer}: HTTP {resp.status_code} {resp.text[:200]}")


async def add_draft_post(
    *, video_id: str, user_id: str, prompt: str, user_token: str
) -> None:
    """Register the generated video as a Draft belonging to `user_id`.

    `video_id` is both the post id and the storage object name, so the app's
    client-side URL builder resolves to the file we just wrote.

    Argument order matches the reducer signature:
    `add_post(id, description, hashtags, video_uid, creator, status)`.
    """
    await _call_reducer(
        "add_post",
        [video_id, prompt[:500], [], video_id, user_id, STATUS_DRAFT],
        user_token,
    )


async def publish_post(*, post_id: str, user_token: str) -> None:
    """Flip a draft to published. Backs the app's Publish button."""
    await _call_reducer("update_post_status", [post_id, STATUS_UPLOADED], user_token)
