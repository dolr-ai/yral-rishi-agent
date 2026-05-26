import jwt
import sentry_sdk
from fastapi import Request, HTTPException

from config import EXPECTED_ISSUERS


def get_current_user(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    if auth_header.startswith(("Bearer ", "bearer ")):
        token = auth_header[7:]
    else:
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format. Expected: Bearer <token>",
        )

    # No signature verification — matches the production Rust service behavior
    try:
        payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": True,
            },
            algorithms=["RS256", "HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.DecodeError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    issuer = payload.get("iss", "")
    if issuer not in EXPECTED_ISSUERS:
        raise HTTPException(status_code=401, detail=f"Invalid token issuer: {issuer}")

    user_id = payload.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing sub")

    sentry_sdk.set_user({"id": user_id})
    return user_id
