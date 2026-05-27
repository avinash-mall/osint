# Why the WebSocket re-validates the session every 60s

**Decision:** [`backend/routers/ws.py`](../../backend/routers/ws.py) re-calls `auth.decode_session_cookie(cookie)` every `WS_SESSION_HEARTBEAT_SECONDS` (default 60s) inside the long-lived pubsub loop. When the cached cookie no longer decodes — typically TTL expiry or signing-key rotation — the WS is closed with code 1008 instead of continuing to deliver Redis events to an expired session.

**Date:** 2026-05-27.

## Context

Plan F's auth gate fires only at handshake. Once a client passes the cookie check, the WS handler enters a `while True` loop that polls Redis and forwards messages until the client disconnects. With `SESSION_TTL_HOURS` defaulting to 12h, an attacker with a valid session at handshake time could keep the connection open for 12+ hours — effectively a session-pinning bug that bypasses HTTP TTL semantics.

The HTTP routes don't have this problem because every request re-decodes the cookie via `Depends(get_current_user)`. The WS is the only long-lived auth surface in the stack.

## Why 60s

- **Cache window awareness.** `decode_session_cookie` is a pure CPU operation (itsdangerous HMAC verify); cost per heartbeat is microseconds. The choice is about *staleness window*, not throughput.
- **TTL expiry detection.** A 60s window means an expired cookie keeps a connection alive for up to 60 extra seconds. That's well below the threshold of any realistic abuse scenario (data exfiltration via event leak takes minutes, not seconds, to be meaningful).
- **Key-rotation detection.** If the operator rotates `SESSION_SECRET`, all WS connections close within 60s of the rotation. HTTP routes pick this up on the next request — WS now matches.
- **Aggressive enough without thrashing.** 30s would catch expiry sooner but doubles the heartbeat wakeups per connection. 5min would miss the realistic abuse window. 60s is the right tradeoff.

## What it does NOT catch

- **In-memory user revocation.** If an admin disables a user account, the session cookie remains cryptographically valid until its TTL elapses. The heartbeat does NOT consult any "is this user still allowed" store — that would need a Redis pub/sub revocation channel (separate spec). Documented limitation; not blocking.
- **Account permission downgrades.** Same reasoning. If a user's role changes from analyst to viewer, their existing WS connection continues to deliver events the new role wouldn't permit. Out of scope; tracking issue if needed.

## How to apply

- Code: [`backend/routers/ws.py`](../../backend/routers/ws.py) — `_HEARTBEAT_SECONDS` module-level constant; the WS handler tracks `last_heartbeat = time.monotonic()` and re-runs `decode_session_cookie(cookie_value)` when the interval elapses. On failure: `await websocket.close(code=1008, reason="session expired")`.
- The cookie value is captured at handshake time and held in handler scope — clients can't update headers mid-connection, so we re-validate the same signed payload against the current signer state.
- Env override: `WS_SESSION_HEARTBEAT_SECONDS` (clamped to ≥5s for sanity).
- Tests: see `test_ws_closes_when_session_invalidates_at_heartbeat` and `test_ws_stays_open_while_session_valid` in [test_reference_platforms_ws_events.py](../../backend/tests/test_reference_platforms_ws_events.py).

## Cross-references

- [why-ws-auth-now-required.md](why-ws-auth-now-required.md) — the handshake-time gate this heartbeat complements.
- HTTP TTL source of truth: [backend/auth.py](../../backend/auth.py) `_ttl_seconds()`.
