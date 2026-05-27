**Decision:** The `/ws` WebSocket endpoint now requires session-cookie authentication. Connections without a valid signed `sentinel_session` cookie are closed with WebSocket code 1008 (Policy Violation) before any data is sent.

## Why
- **Plan F publishes analyst PII over the socket.** Identification events carry `reviewed_by` (the analyst's username). Without auth, an attacker who reaches the backend's WebSocket port (e.g. via a misconfigured nginx) can subscribe to `?topic=identifications` and see who's approving what in real time.
- **Other existing topics carry sensitive data too.** `detections` includes detection metadata and detection_target_candidates updates with reviewer usernames. The pre-Plan-F state was a latent issue; Plan F's PII publishing forces the fix to surface.
- **Browser sessions already carry the cookie automatically** on WebSocket handshake. The frontend `useEventStream` hook needs no change.

## What we rejected
- **Token-based WS auth.** Would require a separate `/api/ws-token` endpoint to issue short-lived tokens. The cookie path is already proven for HTTP; reusing it is the smaller change.
- **Per-topic auth.** A future enhancement could gate certain topics by role (`admin` only for `training:*`), but Plan F just establishes the baseline: any valid session.

## Consequences
- All existing frontend WS consumers (IngestConnect, GaiaMap, FmvPlayer, IdentificationPanel) continue to work because the browser auto-attaches the cookie.
- Backend integration tests that exercise the WS endpoint must log in first (the `_login` fixture).
- A future maintenance task may want to also rate-limit WS reconnects to prevent denial-of-service via reconnect-storm.
- The pre-existing module doc for `routers/ws.py` already claimed auth was required; this commit makes the code match the doc.
