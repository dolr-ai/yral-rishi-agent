"""SpacetimeDB writes — as the user, never as an admin.

Posts live in SpacetimeDB. A generated video becomes a Draft via `add_post_2`,
and the app's Publish button flips it with `update_post_status_2`.

**We act as the calling user.** SpacetimeDB derives a caller `Identity` from a
JWT's `iss` + `sub`, and the app's yral-auth id_token is already minted for this
(`ext_spacetimedb_token`), so forwarding that token makes the write happen as
its owner. The alternative was holding the shared `SPACETIMEDB_ADMIN_TOKEN` —
which can rewrite any user's username, email or subscription plan, a blast
radius wildly out of proportion to creating one post for one person.

Both reducers take **no creator argument**: `add_post_2` reads the OAuth subject
straight off the caller's token (`ctx.sender_auth().jwt().subject()`) and stores
it as `creator_oauth_subject` on `posts_3` — which is exactly the field the app's
Drafts and profile queries filter on. So there is nothing for us to derive, pass,
or get wrong.
"""

import logging

import httpx

import config

logger = logging.getLogger(__name__)

# PostStatus is a SATS tagged enum; the wire form is camelCase with a payload.
# A bare "Draft" is rejected with `unknown variant`.
STATUS_DRAFT = {"draft": []}
STATUS_UPLOADED = {"uploaded": []}


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

    # A refused reducer answers 5xx with a bare reason ("Unauthorized",
    # "PostNotFound"), not a JSON envelope. Never echo the body wholesale — a
    # reducer error can quote its own input back.
    if resp.status_code >= 400:
        raise SpacetimeError(f"{reducer}: HTTP {resp.status_code} {resp.text[:200]}")


async def add_draft_post(
    *, video_id: str, user_id: str, prompt: str, user_token: str
) -> None:
    """Register the generated video as a Draft belonging to the caller.

    `video_id` is both the post id and the storage object name, so the app's
    client-side URL builder resolves to the file we just wrote.

    Argument order matches the reducer:
    `add_post_2(id, description, hashtags, video_uid, status)`.
    """
    await _call_reducer(
        "add_post_2",
        [video_id, prompt[:500], [], video_id, STATUS_DRAFT],
        user_token,
    )
    logger.info("videogen: registered draft post %s for %s", video_id, user_id)


async def publish_post(*, post_id: str, user_token: str) -> None:
    """Flip a draft to published. Backs the app's Publish button.

    The reducer reads the post's own creator and compares it against the sender,
    so ownership needs nothing from us beyond the caller's token.
    """
    await _call_reducer("update_post_status_2", [post_id, STATUS_UPLOADED], user_token)
