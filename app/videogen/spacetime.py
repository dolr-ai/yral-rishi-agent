"""SpacetimeDB writes — as the user, never as an admin.

Posts live in SpacetimeDB. A generated video becomes a Draft via `add_post`, and
the app's Publish button flips it with `update_post_status`.

**We act as the calling user.** SpacetimeDB derives a caller `Identity` from a
JWT's `iss` + `sub`, and the app's yral-auth id_token is already minted for this
(`ext_spacetimedb_token`), so forwarding that token makes the write happen as
its owner. The alternative was holding the shared `SPACETIMEDB_ADMIN_TOKEN` —
which can rewrite any user's username, email or subscription plan, a blast
radius wildly out of proportion to creating one post for one person.

`add_post` and `update_post_status` accept the post's creator as well as an
admin (dolr-ai/yral-bare-metal-kubernetes-cluster#190), which is what makes the
above possible.
"""

import base64
import binascii
import json
import logging

import blake3
import httpx

import config

logger = logging.getLogger(__name__)

# PostStatus is a SATS tagged enum, and the wire form is camelCase with a
# payload: {"draft": []}. A bare "Draft" is rejected with `unknown variant`.
STATUS_DRAFT = {"draft": []}
STATUS_UPLOADED = {"uploaded": []}


class SpacetimeError(RuntimeError):
    """A reducer call failed or was refused."""


def _identity_from_claims(issuer: str, subject: str) -> str:
    """The SpacetimeDB Identity for a JWT's `iss` + `sub`, as a 0x-hex string.

    Reducers that take a `creator: Identity` need the caller's identity as an
    explicit argument, and SpacetimeDB derives it from the token's claims rather
    than exposing it over HTTP — so we compute the same value:

        h        = blake3("{iss}|{sub}")
        identity = 0xc200 || blake3(0xc200 || h[:26])[:4] || h[:26]

    Verified empirically against identities minted by `POST /v1/identity`, whose
    response carries both the identity and a token to read the claims from. The
    four bytes after the `c200` prefix are a checksum over the rest, which is
    why the hash cannot simply be truncated to 30 bytes.
    """
    digest = blake3.blake3(f"{issuer}|{subject}".encode()).digest()
    id_hash = digest[:26]
    checksum = blake3.blake3(b"\xc2\x00" + id_hash).digest()[:4]
    return "0x" + (b"\xc2\x00" + checksum + id_hash).hex()


def identity_for_token(token: str) -> str:
    """Read `iss` + `sub` straight off the token and derive its identity.

    The signature is NOT re-checked here — `auth.get_current_user` already
    verified it against the JWKS before this request reached any handler, and
    verifying twice would mean two places to get it wrong.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return _identity_from_claims(claims["iss"], claims["sub"])
    except (IndexError, ValueError, KeyError, binascii.Error) as e:
        raise SpacetimeError(f"could not read identity from token: {e}") from e


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
    `add_post(id, description, hashtags, video_uid, creator, status)`.

    `creator` must be the caller's SpacetimeDB Identity — NOT the IC principal
    that `user_id` holds. They are different namespaces, and the reducer checks
    `creator == ctx.sender()`, so an IC principal here fails every time.
    """
    creator = identity_for_token(user_token)
    await _call_reducer(
        "add_post",
        [
            video_id,
            prompt[:500],
            [],
            video_id,
            {"__identity__": creator},
            STATUS_DRAFT,
        ],
        user_token,
    )
    logger.info("videogen: registered draft post %s for %s", video_id, user_id)


async def publish_post(*, post_id: str, user_token: str) -> None:
    """Flip a draft to published. Backs the app's Publish button.

    Takes no identity: the reducer reads the post's own creator and compares it
    against the sender.
    """
    await _call_reducer("update_post_status", [post_id, STATUS_UPLOADED], user_token)
