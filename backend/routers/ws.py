"""WebSocket bridge from Redis pub/sub to browser clients.

Auth: the WS handshake must carry the same ``sentinel_session`` cookie the
HTTP API requires. We reuse :func:`auth.decode_session_cookie` so the secret,
salt, and TTL stay in lockstep with the HTTP middleware — no parallel auth
scheme. Connections without a valid session are closed with WebSocket code
1008 (Policy Violation) before ``accept()`` so no Redis messages leak to
unauthenticated clients.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from auth import SESSION_COOKIE, SessionUser, decode_session_cookie

router = APIRouter()


def _get_ws_session_user(websocket: WebSocket) -> Optional[SessionUser]:
    """Extract + verify the session cookie from the WS handshake headers.

    Returns ``None`` on any failure (no cookie, bad signature, expired session).
    Uses the same itsdangerous serializer the HTTP routes use via
    :func:`auth.decode_session_cookie`.
    """
    cookie_header = websocket.headers.get("cookie", "")
    if not cookie_header:
        return None
    cookie_value: Optional[str] = None
    for part in cookie_header.split(";"):
        name, _, val = part.strip().partition("=")
        if name == SESSION_COOKIE:
            cookie_value = val
            break
    if not cookie_value:
        return None
    return decode_session_cookie(cookie_value)


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket, topic: str = "detections"):
    user = _get_ws_session_user(websocket)
    if user is None:
        # Close before accept; per RFC 6455, code 1008 = Policy Violation.
        await websocket.close(code=1008, reason="Unauthorized")
        return
    await websocket.accept()
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = None
    pubsub = None
    try:
        import redis.asyncio as redis
        redis_client = redis.from_url(redis_url, decode_responses=True)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"events:{topic}")
        await websocket.send_json({"type": "connected", "topic": topic})

        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    await websocket.send_text(message["data"])
                await asyncio.sleep(0.1)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            await pubsub.close()
        if redis_client is not None:
            await redis_client.close()
