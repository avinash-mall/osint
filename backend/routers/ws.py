"""WebSocket bridge from Redis pub/sub to browser clients.

Auth: the WS handshake must carry the same ``sentinel_session`` cookie the
HTTP API requires. We reuse :func:`auth.decode_session_cookie` so the secret,
salt, and TTL stay in lockstep with the HTTP middleware — no parallel auth
scheme. Connections without a valid session are closed with WebSocket code
1008 (Policy Violation) before ``accept()`` so no Redis messages leak to
unauthenticated clients.

Long-lived connections are re-validated every ``WS_SESSION_HEARTBEAT_SECONDS``
(default 60s). When the cached session cookie no longer decodes — e.g. its
TTL has elapsed, or the signing secret has rotated — the WS is closed with
code 1008 instead of continuing to deliver events to an expired session.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from auth import SESSION_COOKIE, SessionUser, decode_session_cookie

logger = logging.getLogger(__name__)
router = APIRouter()


_HEARTBEAT_SECONDS = max(5, int(os.getenv("WS_SESSION_HEARTBEAT_SECONDS", "60")))


def _extract_session_cookie(websocket: WebSocket) -> Optional[str]:
    """Pull the ``sentinel_session`` cookie value out of the handshake headers.

    Returns the raw signed cookie string (not the decoded user) so the WS
    handler can re-validate it on each heartbeat tick using
    :func:`auth.decode_session_cookie`.
    """
    cookie_header = websocket.headers.get("cookie", "")
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, _, val = part.strip().partition("=")
        if name == SESSION_COOKIE:
            return val or None
    return None


def _get_ws_session_user(websocket: WebSocket) -> Optional[Tuple[str, SessionUser]]:
    """Extract + verify the session cookie at handshake time.

    Returns ``(cookie_value, user)`` on success so the handler can keep the
    cookie around for periodic re-validation. Returns ``None`` on any failure
    (no cookie, bad signature, expired session).
    """
    cookie_value = _extract_session_cookie(websocket)
    if cookie_value is None:
        return None
    user = decode_session_cookie(cookie_value)
    if user is None:
        return None
    return cookie_value, user


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket, topic: str = "detections"):
    auth_result = _get_ws_session_user(websocket)
    if auth_result is None:
        # Close before accept; per RFC 6455, code 1008 = Policy Violation.
        await websocket.close(code=1008, reason="Unauthorized")
        return
    cookie_value, _user = auth_result
    await websocket.accept()
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = None
    pubsub = None
    last_heartbeat = time.monotonic()
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
                # Session re-validation. We don't re-extract from the WS headers
                # (clients can't update them mid-connection); we re-run the
                # same signer against the cached cookie. itsdangerous fails on
                # TTL expiry or signing-key rotation, either of which means
                # the session is no longer trusted.
                if time.monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS:
                    last_heartbeat = time.monotonic()
                    if decode_session_cookie(cookie_value) is None:
                        logger.info(
                            "ws %s: session expired at heartbeat — closing",
                            topic,
                        )
                        await websocket.close(code=1008, reason="session expired")
                        return
                await asyncio.sleep(0.1)
            except WebSocketDisconnect:
                raise
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            await pubsub.close()
        if redis_client is not None:
            await redis_client.close()
