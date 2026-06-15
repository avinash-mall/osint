# Why all /api reads now require a session

**Date:** 2026-06-12
**Status:** adopted

## Problem

The session middleware gated only mutating verbs (`POST`/`PUT`/`PATCH`/`DELETE`).
Every bulk read — `GET /api/detections`, `/api/detections/geojson-lite`,
`/api/tracks/*`, `/api/graph*`, `/api/fmv/clips` (including stream/source URLs),
`/api/imagery`, `/api/aois`, `/api/operational-entities`, `/api/observations`,
`/api/timeline/events` — was readable with no cookie at all. An unauthenticated
client on the network could pull the entire common operating picture. That
contradicted the platform's own posture: `/ws` already requires a session
precisely because "PII over the socket forced the fix"
([why-ws-auth-now-required.md](why-ws-auth-now-required.md)), yet the identical
data was a plain `curl` away.

## Decision

The middleware ([main.py#L121-L142](../../backend/main.py#L121-L142), renamed
`require_session_on_requests`) now also gates `GET`/`HEAD` on every path under
`/api/`, returning the same `401 {"detail": "not authenticated"}` short-circuit.

A small explicit allowlist (`_PUBLIC_READ_PREFIXES`) stays session-free:

- `/api/auth/*` — the frontend boot probe (`/api/auth/me`) implements its own
  401 semantics; login must obviously be reachable.
- `/api/health` — docker-compose healthchecks probe it with no cookie.
- `/api/system/deployment-mode` — the login screen renders the deployment
  banner before any session exists.
- `/api/ontology/default-prompts` — fetched **service-to-service** by
  inference-sam3 (`ONTOLOGY_BACKEND_URL`, see inference `main.py`); the
  inference container has no session.

Anything new that must be public (pre-auth UI or service-to-service) is added
to the allowlist deliberately — the default for a new route is gated, both
verbs ([conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)).

## Why this shape

- **Middleware, not per-route `Depends`** — same reasoning as the original
  mutation gate: one place, impossible to forget on new routers.
- **Prefix allowlist, not regex/route-name matching** — auditable in five
  lines; `startswith` on a tuple is cheap on the hot path.
- **`/api/` scope only** — `/tiles` (Martin MVT), `/basemap`, `/maps`, `/fmv`
  HLS files are served by nginx/sidecars and never traverse this middleware.

## Client compatibility

- Browser same-origin requests (axios `withCredentials`, `fetch` with
  `credentials: 'include'` since the 2026-06-12 shell/admin audit fix, `<img>`,
  `<video>`/hls.js XHR) all carry the cookie automatically.
- Unit tests that import `main.app` and exercise reads must set a
  `sentinel_session` cookie (see `tests/test_read_auth_gate.py` for the
  pattern); router tests that build their own bare `FastAPI()` app are
  unaffected.

## Known residual exposure

Detection MVT tiles are served by the Martin sidecar through nginx `/tiles`
and bypass the backend entirely, so tile geometry+class properties remain
cookie-free. Gating them needs an nginx `auth_request` sub-request against the
backend session — deliberately out of scope here; revisit if the deployment
threat model requires it.

## Validation

`backend/tests/test_read_auth_gate.py` — 16 offline cases: bulk reads 401
without a cookie, allowlist passes, authenticated reads pass, mutation gate
unchanged.

## Cross-references

- [why-ws-auth-now-required.md](why-ws-auth-now-required.md) — the posture this aligns with
- [why-admin-mutators-require-admin.md](why-admin-mutators-require-admin.md) — role gating on top of this session gate
- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md) — middleware key-symbol entry
