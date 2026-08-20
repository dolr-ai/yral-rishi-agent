import jwt
import sentry_sdk
from fastapi import HTTPException, Request

from config import EXPECTED_ISSUERS, JWKS_URL

# yral-auth v2 signs tokens with ES256; the public key is published at the JWKS
# endpoint and tagged with a `kid`, so key rotation is picked up automatically.
# PyJWKClient fetches the key set and caches it — no per-request network call
# once warm. A real User-Agent is required: the endpoint sits behind Cloudflare,
# which 403s the default `Python-urllib` agent.
_jwks_client = jwt.PyJWKClient(
    JWKS_URL,
    cache_jwk_set=True,
    lifespan=3600,
    headers={"User-Agent": "yral-rishi-agent"},
)


def verify_jwt(token: str) -> dict:
    """Verify a yral-auth v2 ES256 token against the published JWKS.

    Returns the token claims. Raises a ``jwt.PyJWTError`` subclass on any
    verification failure — bad signature, expiry, unknown/absent key, wrong
    issuer, or a missing subject. Shared by the HTTP dependency and the
    WebSocket handler so they verify identically.
    """
    signing_key = _jwks_client.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        options={"verify_aud": False, "verify_exp": True},
    )
    if payload.get("iss", "") not in EXPECTED_ISSUERS:
        raise jwt.InvalidIssuerError(f"Invalid token issuer: {payload.get('iss', '')}")
    if not payload.get("sub", ""):
        raise jwt.InvalidTokenError("Invalid token: missing sub")
    return payload


def get_current_user(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    if not auth_header.startswith(("Bearer ", "bearer ")):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format. Expected: Bearer <token>",
        )
    token = auth_header[7:]

    try:
        payload = verify_jwt(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    user_id = payload["sub"]
    sentry_sdk.set_user({"id": user_id})
    return user_id
