import json
import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_connections: dict[str, list[WebSocket]] = {}
_lock = asyncio.Lock()


async def connect(user_id: str, websocket: WebSocket):
    async with _lock:
        if user_id not in _connections:
            _connections[user_id] = []
        _connections[user_id].append(websocket)


async def disconnect(user_id: str, websocket: WebSocket):
    async with _lock:
        if user_id in _connections:
            _connections[user_id] = [
                ws for ws in _connections[user_id] if ws is not websocket
            ]
            if not _connections[user_id]:
                del _connections[user_id]


async def _send_to_user(user_id: str, message: str):
    if user_id not in _connections:
        return

    dead_connections = []
    for ws in _connections.get(user_id, []):
        try:
            await ws.send_text(message)
        except Exception:
            dead_connections.append(ws)

    if dead_connections:
        async with _lock:
            if user_id in _connections:
                _connections[user_id] = [
                    ws for ws in _connections[user_id] if ws not in dead_connections
                ]
                if not _connections[user_id]:
                    del _connections[user_id]


async def broadcast_new_message(
    user_id: str,
    conversation_id: str,
    message: dict,
    influencer: dict,
    unread_count: int,
):
    event = json.dumps(
        {
            "event": "new_message",
            "data": {
                "conversation_id": conversation_id,
                "message": message,
                "influencer": influencer,
                "unread_count": unread_count,
            },
        }
    )
    await _send_to_user(user_id, event)


async def broadcast_conversation_read(user_id: str, conversation_id: str, read_at: str):
    event = json.dumps(
        {
            "event": "conversation_read",
            "data": {
                "conversation_id": conversation_id,
                "unread_count": 0,
                "read_at": read_at,
            },
        }
    )
    await _send_to_user(user_id, event)


async def broadcast_typing_status(
    user_id: str,
    conversation_id: str,
    influencer_id: str,
    is_typing: bool,
):
    event = json.dumps(
        {
            "event": "typing_status",
            "data": {
                "conversation_id": conversation_id,
                "influencer_id": influencer_id,
                "is_typing": is_typing,
            },
        }
    )
    await _send_to_user(user_id, event)
