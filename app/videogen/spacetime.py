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

import base64
import binascii
import json
import logging

import httpx

import config

logger = logging.getLogger(__name__)

# PostStatus is a SATS tagged enum; the wire form is camelCase with a payload.
# A bare "Draft" is rejected with `unknown variant`.
STATUS_DRAFT = {"draft": []}
STATUS_UPLOADED = {"uploaded": []}

# `Option<T>` is a tagged enum on the wire too, and takes exactly the same shape
# as the status above — never a bare value and never JSON null. The live schema
# declares `post_as_ai_account_id` as a sum of `some(String) | none`, and sending
# the bare string is what made `add_post_2` answer `HTTP 400 invalid arguments
# ... expected sum type` on 2026-08-27, losing a generated video at the last
# step. Note `null` happens to parse as `none`, so only the `some` side broke —
# which is why generating on your own profile kept working and generating as a
# bot never did.
NO_AI_ACCOUNT = {"none": []}


def posting_as(ai_account_id: str) -> dict:
    """The `some` side of that Option."""
    return {"some": ai_account_id}


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


def oauth_subject_from_token(token: str) -> str:
    """The `sub` claim of a token — who the caller is.

    The signature is NOT re-checked here. `auth.get_current_user` already
    verified it against the JWKS before the request reached any handler, and
    verifying twice would mean two places to get it wrong. This only needs to
    read a claim we have already established is trustworthy.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["sub"]
    except (IndexError, ValueError, KeyError, binascii.Error) as e:
        raise SpacetimeError(f"could not read the subject from the token: {e}") from e


async def add_draft_post(
    *, video_id: str, owner_of_video: str, prompt: str, user_token: str
) -> None:
    """Register the generated video as a Draft belonging to `owner_of_video`.

    `owner_of_video` is usually an AI influencer — the app generates videos with
    `userId = botPrincipal`, so the video is by the bot rather than by the person
    who asked for it. It is only the caller themselves when someone generates a
    video on their own profile.

    `add_post_2` takes the account to post as, and refuses an account the
    caller's token does not list as theirs. Passing the caller's *own* subject
    would be refused too — a person does not own themselves — so that case sends
    `none`, which means "post as me".

    Both sides go on the wire as tagged variants, not bare values; see
    `NO_AI_ACCOUNT`.

    `video_id` is both the post id and the storage object name, so the app's
    client-side URL builder resolves to the file we just wrote.
    """
    caller = oauth_subject_from_token(user_token)
    posting_as_self = owner_of_video == caller

    await _call_reducer(
        "add_post_2",
        [
            video_id,
            prompt[:500],
            [],
            video_id,
            STATUS_DRAFT,
            NO_AI_ACCOUNT if posting_as_self else posting_as(owner_of_video),
        ],
        user_token,
    )
    logger.info(
        "videogen: registered draft post %s as %s",
        video_id,
        f"{caller} (self)" if posting_as_self else owner_of_video,
    )


async def publish_post(*, post_id: str, user_token: str) -> None:
    """Flip a draft to published. Backs the app's Publish button.

    The reducer reads the post's own creator and compares it against the sender,
    so ownership needs nothing from us beyond the caller's token.
    """
    await _call_reducer("update_post_status_2", [post_id, STATUS_UPLOADED], user_token)
