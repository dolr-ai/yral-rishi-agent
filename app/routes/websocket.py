import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from services import websocket_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["WebSocket"])


@router.websocket("/ws/inbox/{user_id}")
async def ws_inbox(websocket: WebSocket, user_id: str, token: str = Query(default="")):
    if not token:
        await websocket.close(code=4001, reason="Missing authentication token")
        return

    import jwt as pyjwt
    from config import EXPECTED_ISSUERS

    try:
        payload = pyjwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": True,
            },
            algorithms=["RS256", "HS256"],
        )
    except Exception:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    issuer = payload.get("iss", "")
    if issuer not in EXPECTED_ISSUERS:
        await websocket.close(code=4001, reason="Invalid token issuer")
        return

    token_user_id = payload.get("sub", "")
    if not token_user_id:
        await websocket.close(code=4001, reason="Invalid token: missing sub")
        return

    if token_user_id != user_id:
        await websocket.close(code=4003, reason="Forbidden")
        return

    await websocket.accept()
    await websocket_manager.connect(user_id, websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await websocket_manager.disconnect(user_id, websocket)


@router.get("/ws/docs")
async def ws_docs():
    return {
        "new_message": {
            "event": "new_message",
            "data": {
                "conversation_id": "string",
                "message": "MessageResponse object",
                "influencer": {
                    "id": "string",
                    "display_name": "string",
                    "avatar_url": "string or null",
                    "is_online": True,
                },
                "unread_count": 0,
            },
        },
        "conversation_read": {
            "event": "conversation_read",
            "data": {
                "conversation_id": "string",
                "unread_count": 0,
                "read_at": "ISO timestamp",
            },
        },
        "typing_status": {
            "event": "typing_status",
            "data": {
                "conversation_id": "string",
                "influencer_id": "string",
                "is_typing": True,
            },
        },
    }
