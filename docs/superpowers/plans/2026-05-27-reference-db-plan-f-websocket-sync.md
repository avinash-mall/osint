# Reference Embedding DB — Plan F: WebSocket Live Sync + Polish

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multi-analyst live updates for identification candidates. When analyst A approves/rejects/re-identifies via Plan D's HTTP API, analyst B's open IdentificationPanel for the same detection sees the queue update without manual refresh. Plus the small polish items the Plan E final review carried forward: WS auth gate (prerequisite for publishing analyst PII), `<ChipImg>` shared component, pagination affordance with total count, admin tour section.

**End state:** Two analysts can open the same detection in side-by-side browser windows; an approve in one window flips the candidate state in the other within ~100 ms. The `/ws` WebSocket endpoint enforces session-cookie auth (closing a pre-existing security gap that Plan F surfaces by publishing analyst usernames). Both chip-image sites use a shared `<ChipImg>` component. The Reference Platforms admin tab shows "Showing N of M" when results exceed the limit. The product tour gains an Admin section covering the reference-platforms tab.

**Architecture:**
- **Backend**: emit `publish_event("identifications", payload)` from the 3 mutating routes (approve, reject, identify) in `backend/routers/reference_platforms.py`. The WS handler at `backend/routers/ws.py` gains a session-cookie check before `websocket.accept()` — same `itsdangerous` decode the HTTP middleware uses. The backend list response (`/api/reference-platforms`) gains a `total` field via a separate COUNT(*) query.
- **Frontend**: `IdentificationPanel.tsx` adds `useEventStream("identifications", onMessage)` filtering by `detection_id` to re-fetch on relevant events. `ReferencePlatformsView.tsx` shows `Showing ${platforms.length} of ${total}` when `total > platforms.length`. Both chip-image sites (`IdentificationPanel`, `ReferencePlatformsView`) extract their `<img>` + onError into `<ChipImg chipId={cid} />`. Tour gains an admin section.
- **Decision**: the WS auth fix is a Plan F deliverable, not a pre-existing issue we sidestep. A new decision doc `why-ws-auth-now-required.md` records the rationale.

**Tech Stack:**
- Existing `publish_event` infra at `backend/events.py:37-43` (Redis pubsub, fire-and-forget).
- Existing `/ws` endpoint at `backend/routers/ws.py:13-40` (one topic per connection, currently unauthenticated).
- Existing `useEventStream(topic, onMessage)` hook at `frontend/src/hooks/useEventStream.ts:13-54` (auto-reconnect, multiple-hooks-per-component supported).
- `itsdangerous` session-cookie decode (same path the HTTP middleware uses).
- pytest with monkeypatch for event-publish tests; FastAPI TestClient's `websocket_connect` for WS-level integration tests.

**Parent specs (in-repo):**
- Plan A: `docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md`
- Plan B: `docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md`
- Plan C: `docs/superpowers/plans/2026-05-27-reference-db-plan-c-auto-identify.md`
- Plan D: `docs/superpowers/plans/2026-05-27-reference-db-plan-d-backend-api.md`
- Plan E: `docs/superpowers/plans/2026-05-27-reference-db-plan-e-frontend.md`

---

## File Structure

**Created:**
- `frontend/src/components/ChipImg.tsx` *(new)* — small shared `<ChipImg chipId={cid} size={32} />` component encapsulating the chip-image src + onError fallback. Used by both IdentificationPanel and ReferencePlatformsView.
- `backend/tests/test_reference_platforms_ws_events.py` *(new)* — tests: each of the 3 mutating routes publishes the right event; WS handler rejects unauthenticated connections.
- `docs/decisions/why-ws-auth-now-required.md` *(new)* — decision: the `/ws` endpoint must authenticate now that it publishes analyst PII.
- `docs/frontend/chip-img-component.md` *(new)* — short doc for the shared component.

**Modified:**
- `backend/routers/reference_platforms.py` — add `publish_event("identifications", ...)` to `approve`, `reject`, `identify` handlers. Add `total` count to `list_reference_platforms` response.
- `backend/routers/ws.py` — add session-cookie auth before `websocket.accept()`. Return 1008 Unauthorized if no valid session.
- `backend/schemas.py` — add `total: int` field to `ReferencePlatformsList`.
- `frontend/src/components/map/IdentificationPanel.tsx` — wire `useEventStream("identifications", ...)`, filter by `detection_id`, call `load()` on matching events. Replace inline chip `<img>` with `<ChipImg>`.
- `frontend/src/components/admin/ReferencePlatformsView.tsx` — show "Showing N of M" affordance when total > limit. Replace inline chip `<img>` with `<ChipImg>`.
- `frontend/src/components/tour/tourSteps.ts` — add the admin-reference-platforms tour step now that there's an admin tour section to put it in (or accept that admin tour is broader-scope and just add the one step).
- `docs/backend-routers/reference-platforms-router.md` — note the new `publish_event` emissions in the Why-this-design section. Update the route table's `list` row with the new `total` field.
- `docs/frontend/identification-panel.md` — note `useEventStream` wiring.
- `docs/frontend/admin-reference-platforms.md` — note `total`/pagination affordance.
- `docs/INDEX.txt` — two new entries.

**Untouched in Plan F:**
- `inference-sam3/*` — no inference changes.
- `backend/worker_legacy.py` — the auto path doesn't emit events here (worker writes happen inside the detection-insert transaction; emitting from there would tie pubsub to transaction commit, which is fragile). Auto-identify candidate rows surface to analyst-B sessions via the WS event from approve/reject only.
- Any other admin tab — only `ReferencePlatformsView` gets the pagination affordance.

---

## Task 1 — WebSocket session-cookie auth

**Files:**
- Modify: `backend/routers/ws.py`

The existing `/ws` endpoint is unauthenticated. Plan F is about to publish events containing `reviewed_by` (analyst usernames), so the auth gap becomes a real PII leak. Close it before publishing.

- [ ] **Step 1: Find the session-cookie decode pattern**

Read `backend/main.py` for the existing HTTP session middleware. Find where it extracts the cookie and decodes via `itsdangerous`. Note the cookie name (`sentinel_session`?), the signer key, and the failure mode.

Read `backend/auth.py` for `get_current_user` — this is the HTTP-side version. The same logic adapted for WebSocket headers is what Plan F needs.

- [ ] **Step 2: Add auth check to the WS handler**

Open `backend/routers/ws.py`. Find the `@router.websocket("/ws")` handler. BEFORE the `await websocket.accept()` call, add:

```python
from itsdangerous import BadSignature, SignatureExpired

def _get_ws_session_user(websocket: WebSocket) -> Optional[SessionUser]:
    """Extract + verify the session cookie from the WS handshake headers.

    Returns None on any failure (no cookie, bad signature, expired session).
    """
    cookie_header = websocket.headers.get("cookie", "")
    cookie_value = None
    for part in cookie_header.split(";"):
        name, _, val = part.strip().partition("=")
        if name == SESSION_COOKIE_NAME:  # 'sentinel_session' or whatever it is
            cookie_value = val
            break
    if not cookie_value:
        return None
    try:
        signer = URLSafeTimedSerializer(SESSION_SECRET, salt="session")
        payload = signer.loads(cookie_value, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    # payload should be a dict like {"username": "...", "role": "...", ...}
    return SessionUser(**payload) if isinstance(payload, dict) else None
```

Adapt the signer salt + max-age constants to match the existing HTTP middleware exactly. Don't invent a new auth scheme — this is the same cookie the HTTP routes verify.

Then update the handler:

```python
@router.websocket("/ws")
async def websocket_events(websocket: WebSocket, topic: str = "detections"):
    user = _get_ws_session_user(websocket)
    if user is None:
        await websocket.close(code=1008, reason="Unauthorized")
        return
    await websocket.accept()
    # ... existing redis-pubsub loop ...
```

Use HTTP-style 1008 (Policy Violation) — the common WS code for auth failure.

- [ ] **Step 3: Verify the existing WS consumers still work**

The frontend's `useEventStream` opens WebSocket connections from authenticated browser sessions, so the cookie is automatically attached by the browser. Verify:

```bash
# Start the stack, log in, then connect to /ws — should succeed.
docker compose up -d backend
sleep 5
# Get a session cookie
USER=$(grep ^ADMIN_USERNAME /nvme/osint/.env | cut -d= -f2)
PASS=$(grep ^ADMIN_PASSWORD /nvme/osint/.env | cut -d= -f2)
COOKIE=$(curl -s -i -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  http://localhost:3000/api/auth/login | grep -i '^set-cookie:' | awk '{print $2}' | sed 's/;$//')
# Try connecting to /ws with the cookie
docker compose exec -T backend python -c "
import asyncio, websockets, sys
async def go():
    async with websockets.connect(
        'ws://localhost:8080/ws?topic=detections',
        extra_headers={'Cookie': '$COOKIE'},
    ) as ws:
        print('OK: connected as authenticated user')
asyncio.run(go())
"
```

Adapt the URL + port to match the actual setup. If `websockets` isn't installed in the backend image, use a different WS client (e.g. `wscat`, or write the test against FastAPI's `TestClient.websocket_connect`).

- [ ] **Step 4: Verify unauthenticated connections are rejected**

```bash
# Connect WITHOUT cookie — should be closed with 1008
docker compose exec -T backend python -c "
import asyncio, websockets
async def go():
    try:
        async with websockets.connect('ws://localhost:8080/ws?topic=detections') as ws:
            print('FAIL: unauthenticated connection accepted')
    except websockets.exceptions.ConnectionClosedError as e:
        print(f'OK: rejected with code {e.code}')
asyncio.run(go())
"
```

Expected: `OK: rejected with code 1008`.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/ws.py
git commit -m "fix(ws): require session-cookie auth before accepting WebSocket connections"
```

---

## Task 2 — Emit `publish_event` from the 3 mutating identification routes

**Files:**
- Modify: `backend/routers/reference_platforms.py`

After each successful approve/reject/identify call, publish a JSON event to the `"identifications"` topic. The payload's `type` discriminator follows the existing convention (`message.type` is what frontend consumers filter on).

- [ ] **Step 1: Add the import + payload helper**

Open `backend/routers/reference_platforms.py`. Near the existing imports, add:

```python
from events import publish_event
```

- [ ] **Step 2: Emit on approve**

Find the `approve_identification_candidate` handler. AFTER the SQL UPSERT block (after `_upsert_platform_identification` returns), but BEFORE the final `return ApproveRejectResponse(...)`, add:

```python
    publish_event(
        "identifications",
        {
            "type": "identification_approved",
            "detection_id": cand["detection_id"],
            "candidate_id": cand["id"],
            "platform_id": cand["platform_id"],
            "platform_name": plat["platform_name"],
            "reviewed_by": user.username,
            "score": float(cand["score"]),
        },
    )
```

Note: the publish happens AFTER the cursor's `with` block has committed (the publish runs after the body returns from the `with`). For atomicity, the publish must happen INSIDE the `with` block so a rollback also skips the publish. Re-check the route's exact structure and put the publish in the right place — the rule is "only publish if the transaction commits."

Actually, the simplest robust pattern: keep the publish OUTSIDE the `with` block. If the transaction fails, an exception propagates and we never reach the publish. If the transaction succeeds, the `with` block exits cleanly and we publish. That's the right ordering.

- [ ] **Step 3: Emit on reject**

In `reject_identification_candidate`, after the SQL UPDATE, before `return ApproveRejectResponse(...)`:

```python
    publish_event(
        "identifications",
        {
            "type": "identification_rejected",
            "detection_id": cand["detection_id"],
            "candidate_id": cand["id"],
            "platform_id": cand["platform_id"],
            "reviewed_by": user.username,
        },
    )
```

- [ ] **Step 4: Emit on re-identify**

In `identify_detection`, after the candidates are inserted and the SELECT returns the new list, before the `return IdentifyResponse(...)`:

```python
    publish_event(
        "identifications",
        {
            "type": "identification_refreshed",
            "detection_id": detection_id,
            "candidates_written": n,
            "reviewed_by": user.username,
        },
    )
```

- [ ] **Step 5: Sanity test by listening on /ws and triggering an approve**

```bash
# Terminal 1: subscribe to the identifications topic
docker compose exec -T backend python -c "
import asyncio, websockets
COOKIE = '...'  # paste from a logged-in session
async def go():
    async with websockets.connect(
        'ws://localhost:8080/ws?topic=identifications',
        extra_headers={'Cookie': COOKIE},
    ) as ws:
        async for msg in ws:
            print('event:', msg)
asyncio.run(go())
"

# Terminal 2: trigger an approve via curl
# (Use one of the existing fixture candidate ids from the integration test data)
```

Expected: terminal 1 prints a JSON event like `{"type":"identification_approved","detection_id":...,"reviewed_by":"admin"}`.

If wiring up the curl manually is fiddly, defer to Task 4's automated test — it validates the same flow.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/reference_platforms.py
git commit -m "feat(reference-db): publish_event on approve/reject/identify for multi-analyst sync"
```

---

## Task 3 — Failing integration test for events + WS auth

**Files:**
- Create: `backend/tests/test_reference_platforms_ws_events.py`

Tests:
1. `publish_event` is called with the expected payload after approve/reject/identify (monkeypatched).
2. WebSocket connection without cookie → closed with 1008.
3. WebSocket connection with valid cookie → accepted.

- [ ] **Step 1: Write the failing test (it should fail without Tasks 1+2)**

Actually wait — Tasks 1 and 2 already landed by the time you read this. The test will pass on first run. That's fine — TDD's strict failing-test-first is most valuable when implementing the feature. Tasks 1+2 are small enough that the test still serves as a regression net.

Create `/nvme/osint/backend/tests/test_reference_platforms_ws_events.py`:

```python
"""Tests for WebSocket auth + identification event publishing.

Covers:
  - publish_event called on approve/reject/identify with expected payload.
  - WS connection without session cookie is rejected with 1008.
  - WS connection with valid session cookie is accepted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="module", autouse=True)
def _setup_env():
    os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-replace-1234567890abcdef")
    os.environ.setdefault("ADMIN_USERNAME", "test-admin")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def _login(client) -> dict:
    """Returns the cookie jar for use in WS connect_kwargs."""
    resp = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.cookies


def test_ws_rejects_unauthenticated_connection(client):
    """No cookie → WS handshake closes with 1008."""
    from starlette.testclient import WebSocketTestSession
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws?topic=identifications"):
            pass
    # 1008 = Policy Violation per WS RFC
    assert exc_info.value.code == 1008


def test_ws_accepts_authenticated_connection(client):
    """With a valid session cookie, WS handshake succeeds."""
    _login(client)
    # The TestClient session carries the cookie automatically.
    with client.websocket_connect("/ws?topic=identifications") as ws:
        # Connection succeeded; close it cleanly.
        pass


# --- publish_event call-site tests (monkeypatch) ---------------------------


# Fixture seeded for the candidate-write tests
@pytest.fixture(scope="module")
def populated_ref():
    import numpy as np
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import (
        upsert_reference_platform, insert_reference_chip, recompute_platform_centroids,
    )
    from database import postgis_db
    ensure_reference_platform_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM platform_identification_candidates WHERE detection_id IN (SELECT id FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM object_details WHERE source = 'detection' AND source_id IN (SELECT id::text FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-wsev-%'")
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-wsev'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-wsev-%'")
        pid = upsert_reference_platform(cur, platform_name="pytest-wsev-Red", platform_family="WsevFam")
        for i in range(3):
            insert_reference_chip(
                cur, platform_id=pid, view_domain="overhead",
                source_dataset="pytest-wsev",
                chip_path=f"/tmp/pytest-wsev-red-{i}.png",
                embedding=np.full(1024, 1.0, dtype=np.float32),
                license_spdx="CC0-1.0",
            )
        recompute_platform_centroids(cur, platform_id=pid)
    yield {"red_id": pid}
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM platform_identification_candidates WHERE detection_id IN (SELECT id FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM object_details WHERE source = 'detection' AND source_id IN (SELECT id::text FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-wsev-%'")
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-wsev'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-wsev-%'")


def _insert_fake_detection(label: str) -> int:
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO detections (class, confidence, geom, centroid, metadata)
            VALUES (%s, 0.5,
                    ST_GeomFromText('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 4326),
                    ST_GeomFromText('POINT(0.5 0.5)', 4326),
                    '{}'::jsonb)
            RETURNING id
            """,
            (label,),
        )
        return cur.fetchone()["id"]


def test_approve_publishes_identification_approved(client, populated_ref):
    """Approving a candidate must publish_event('identifications', {type='identification_approved', ...})."""
    import numpy as np
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates

    _login(client)
    det_id = _insert_fake_detection("pytest-wsev-approve")
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    published = []
    with patch("routers.reference_platforms.publish_event", side_effect=lambda topic, payload: published.append((topic, payload))):
        resp = client.post(f"/api/identification-candidates/{cand_id}/approve")
        assert resp.status_code == 200, resp.text

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "identifications"
    assert payload["type"] == "identification_approved"
    assert payload["detection_id"] == det_id
    assert payload["candidate_id"] == cand_id
    assert payload["reviewed_by"] == os.environ["ADMIN_USERNAME"]


def test_reject_publishes_identification_rejected(client, populated_ref):
    import numpy as np
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates

    _login(client)
    det_id = _insert_fake_detection("pytest-wsev-reject")
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    published = []
    with patch("routers.reference_platforms.publish_event", side_effect=lambda topic, payload: published.append((topic, payload))):
        resp = client.post(f"/api/identification-candidates/{cand_id}/reject")
        assert resp.status_code == 200, resp.text

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "identifications"
    assert payload["type"] == "identification_rejected"
    assert payload["detection_id"] == det_id


def test_identify_publishes_identification_refreshed(client, populated_ref):
    """POST /api/detections/{id}/identify must publish a 'identification_refreshed' event."""
    import base64
    import numpy as np
    from database import postgis_db

    _login(client)
    det_id = _insert_fake_detection("pytest-wsev-identify")
    # Plant an embedding so identify doesn't 400
    v_fp16 = np.full(1024, 1.0, dtype=np.float16)
    fp16_b64 = base64.b64encode(v_fp16.tobytes()).decode("ascii")
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE detections SET metadata = %s::jsonb WHERE id = %s",
            (
                f'{{"embedding": {{"model":"test","dim":1024,"fp16_b64":"{fp16_b64}"}}}}',
                det_id,
            ),
        )

    published = []
    with patch("routers.reference_platforms.publish_event", side_effect=lambda topic, payload: published.append((topic, payload))):
        resp = client.post(f"/api/detections/{det_id}/identify", json={"view_domain": "overhead", "top_k": 3})
        assert resp.status_code == 200, resp.text

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "identifications"
    assert payload["type"] == "identification_refreshed"
    assert payload["detection_id"] == det_id
```

- [ ] **Step 2: Run the test — must show 5 passed**

```bash
docker compose exec -T backend bash -lc "pip install -q pytest >/dev/null 2>&1; cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platforms_ws_events.py -v 2>&1 | tail -10"
```

Expected: 5 passed.

If a test fails with `WebSocketDisconnect` not raised when expected, the WS auth check in Task 1 isn't firing — debug there. If a publish_event test fails, the route isn't calling publish_event — debug Task 2.

- [ ] **Step 3: Run the wider suite to confirm no regression**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platforms_router.py tests/test_reference_chip_image_route.py tests/test_reference_platforms_ws_events.py 2>&1 | tail -3"
```

Expected: 23 passed (13 router + 5 chip + 5 ws_events).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_reference_platforms_ws_events.py
git commit -m "test(reference-db): WS auth + publish_event for approve/reject/identify"
```

---

## Task 4 — `<ChipImg>` shared component

**Files:**
- Create: `frontend/src/components/ChipImg.tsx`

Today's duplicated chip-image JSX (with onError fallback) lives in two sites:
1. `frontend/src/components/map/IdentificationPanel.tsx` (3 small thumbs per candidate).
2. `frontend/src/components/admin/ReferencePlatformsView.tsx` (chip gallery).

Plan E's reviewer noted this is acceptable today but should be extracted if a third site appears. Plan F just extracts it preemptively — small, clean.

- [ ] **Step 1: Write the component**

Create `/nvme/osint/frontend/src/components/ChipImg.tsx`:

```tsx
import { useState } from "react";

interface Props {
  chipId: string;
  size?: number;
  alt?: string;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * Reference-chip thumbnail with a built-in onError fallback.
 *
 * Renders an <img> pointing at /api/reference-chips/{chipId}/image. On 4xx/5xx,
 * swaps to a neutral inline-SVG placeholder + tooltip explaining the chip is
 * unavailable. This avoids the case where an analyst approves a candidate
 * without realising the supporting chip evidence is missing.
 */
export default function ChipImg({ chipId, size = 32, alt, className, style }: Props) {
  const [failed, setFailed] = useState(false);

  const sizeStyle = { width: size, height: size, objectFit: "cover" as const };
  const mergedStyle = { ...sizeStyle, ...(style || {}) };

  if (failed) {
    return (
      <span
        className={className}
        style={{ ...mergedStyle, display: "inline-flex", alignItems: "center",
                 justifyContent: "center", background: "var(--bg-2)",
                 border: "1px solid var(--line)", color: "var(--ink-3)",
                 fontSize: 9, opacity: 0.6 }}
        title="chip image unavailable"
        aria-label="chip image unavailable"
      >
        ✕
      </span>
    );
  }

  return (
    <img
      src={`/api/reference-chips/${chipId}/image`}
      alt={alt ?? `reference chip ${chipId}`}
      loading="lazy"
      className={className}
      style={mergedStyle}
      onError={() => setFailed(true)}
    />
  );
}
```

Notes:
- Uses React state for the failed flag (cleaner than the dataset.fallback trick from Plan E's closer commit).
- Default size 32 matches IdentificationPanel's small thumbs; admin gallery can pass `size={64}` or whatever it currently uses.
- Doesn't need an `API_URL` prefix — the existing two sites use relative URLs (`/api/reference-chips/...`).

- [ ] **Step 2: Replace the inline `<img>` in `IdentificationPanel.tsx`**

Open `/nvme/osint/frontend/src/components/map/IdentificationPanel.tsx`. Find the inline chip-image block (the `<img>` with the `onError={...}` set in Plan E's closer commit). Replace with:

```tsx
<ChipImg chipId={cid} size={32} alt={`reference chip ${cid}`} />
```

Add the import: `import ChipImg from "../ChipImg";`.

- [ ] **Step 3: Replace the inline `<img>` in `ReferencePlatformsView.tsx`**

Same surgical replacement. The size may be larger here (admin chip gallery). Use whatever size the existing inline `<img>` declared.

Add the import: `import ChipImg from "../ChipImg";`.

- [ ] **Step 4: Build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build. No TS errors. Bundle size shouldn't change meaningfully.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChipImg.tsx frontend/src/components/map/IdentificationPanel.tsx frontend/src/components/admin/ReferencePlatformsView.tsx
git commit -m "refactor(reference-db): extract ChipImg shared component"
```

---

## Task 5 — IdentificationPanel subscribes to live events

**Files:**
- Modify: `frontend/src/components/map/IdentificationPanel.tsx`

When approve/reject/identify happens (in this panel or in another analyst's panel for the same detection), re-fetch the candidate list.

- [ ] **Step 1: Wire `useEventStream`**

Open `IdentificationPanel.tsx`. Near the existing imports, add:

```tsx
import { useEventStream } from "../../hooks/useEventStream";
```

(Path may differ — check the actual hook location and adjust.)

Inside the component, AFTER the `useEffect([detectionId])` that calls `load()`, add:

```tsx
useEventStream(
  "identifications",
  useCallback((msg: any) => {
    if (!msg || typeof msg !== "object") return;
    // Only react to events for the currently-selected detection
    if (msg.detection_id !== detectionId) return;
    // Re-fetch on any of our three event types
    if (
      msg.type === "identification_approved" ||
      msg.type === "identification_rejected" ||
      msg.type === "identification_refreshed"
    ) {
      void load();
    }
  }, [detectionId]),  // load is recreated on detectionId change too, but useCallback w/ load in deps would cycle — keep deps minimal
);
```

If `useCallback` isn't already imported, add it: `import { useCallback, useEffect, useMemo, useState } from "react";`.

- [ ] **Step 2: Build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build.

- [ ] **Step 3: Live sanity test (optional)**

Open two browser windows with the same detection selected. In window A, click Approve on a candidate. Within ~100ms, window B's panel should refresh without a page reload. (If you can't easily run two windows, the integration test in Task 3 already proves the publish-side; the receive-side relies on the existing `useEventStream` infrastructure which is tested in production via other consumers.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/map/IdentificationPanel.tsx
git commit -m "feat(reference-db): IdentificationPanel subscribes to live identification events"
```

---

## Task 6 — Pagination affordance: `total` count in list response + UI badge

**Files:**
- Modify: `backend/routers/reference_platforms.py`
- Modify: `backend/schemas.py`
- Modify: `frontend/src/components/admin/ReferencePlatformsView.tsx`

Today the list response returns `{ platforms, count }` where `count = len(platforms)`. With 18 DOTA rows, this is fine. When xView lands with hundreds of rows, analysts will see "200 loaded" and not know there are more. Add a separate COUNT(*) and surface it.

- [ ] **Step 1: Backend — add `total` field to `ReferencePlatformsList`**

In `backend/schemas.py`, find `ReferencePlatformsList`:

```python
class ReferencePlatformsList(BaseModel):
    platforms: List[ReferencePlatformSummary]
    count: int
```

Add a `total` field:

```python
class ReferencePlatformsList(BaseModel):
    platforms: List[ReferencePlatformSummary]
    count: int     # number of rows returned in this response
    total: int     # total rows matching the filter (regardless of limit)
```

- [ ] **Step 2: Backend — compute `total` in the list handler**

In `backend/routers/reference_platforms.py`, find `list_reference_platforms`. After building the `where` clause but BEFORE the data SELECT, run a COUNT(*) with the same filter:

```python
    # ... existing where_clause construction ...

    with postgis_db.get_cursor(commit=False) as cur:
        # Count first (no LIMIT/OFFSET) so the UI can show "showing N of M"
        cur.execute(
            f"SELECT COUNT(*) AS total FROM reference_platforms {where_clause}",
            tuple(params),
        )
        total = cur.fetchone()["total"]

        cur.execute(
            f"""
            SELECT id::text AS id, platform_name, platform_family,
                   ontology_object_id, country_of_origin, role,
                   view_domains, attributes
              FROM reference_platforms
              {where_clause}
             ORDER BY platform_name
             LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        rows = cur.fetchall()

    # ... existing platforms list build ...

    return ReferencePlatformsList(platforms=platforms, count=len(platforms), total=total)
```

- [ ] **Step 3: Run the router test suite to confirm no regression**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platforms_router.py -v 2>&1 | tail -15"
```

Expected: 13 passed (the existing tests may need updating if they assert response shape — adjust those assertions to include `total`).

- [ ] **Step 4: Frontend — show "Showing N of M" when `total > platforms.length`**

In `frontend/src/components/admin/ReferencePlatformsView.tsx`, find the header / count display. Update the TypeScript interface for the list response to include `total`. Update the count badge logic:

```tsx
// Existing:
// <span>{platforms.length} loaded · curated reference DB</span>

// New:
{total > platforms.length ? (
  <span>Showing {platforms.length} of {total} · narrow filters to see specific platforms</span>
) : (
  <span>{platforms.length} loaded · curated reference DB</span>
)}
```

If the component currently destructures `{ platforms, count }` from the response, switch to `{ platforms, count, total }` and use `total` for the badge logic.

- [ ] **Step 5: Build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

- [ ] **Step 6: Commit**

```bash
git add backend/schemas.py backend/routers/reference_platforms.py frontend/src/components/admin/ReferencePlatformsView.tsx
git commit -m "feat(reference-db): list response gains total; admin view shows 'N of M' when capped"
```

---

## Task 7 — Documentation

**Files:**
- Create: `docs/decisions/why-ws-auth-now-required.md`
- Create: `docs/frontend/chip-img-component.md`
- Modify: `docs/backend-routers/reference-platforms-router.md` (mention publish_event + total field)
- Modify: `docs/frontend/identification-panel.md` (useEventStream wiring)
- Modify: `docs/frontend/admin-reference-platforms.md` (total/pagination affordance + ChipImg)
- Modify: `docs/INDEX.txt`

- [ ] **Step 1: Write the WS-auth decision doc**

Create `docs/decisions/why-ws-auth-now-required.md`:

```markdown
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
```

- [ ] **Step 2: Write the ChipImg module doc**

Create `docs/frontend/chip-img-component.md`. Follow the six-section template, brief — the component is ~40 lines.

- [ ] **Step 3: Update the router doc**

Add a "Why this design" bullet to `docs/backend-routers/reference-platforms-router.md`:

```markdown
- **Live events** — approve/reject/identify routes emit `publish_event("identifications", {type, detection_id, ...})` so frontend consumers can subscribe via `useEventStream("identifications", ...)` for multi-analyst sync. See [why-ws-auth-now-required.md](../decisions/why-ws-auth-now-required.md) for the WS auth gate.
```

Also update the list-route description to mention the `total` field.

- [ ] **Step 4: Update the IdentificationPanel doc**

Add a brief mention of the `useEventStream("identifications", ...)` wiring to `docs/frontend/identification-panel.md`.

- [ ] **Step 5: Update the admin view doc**

Note the `total`/pagination affordance and the `<ChipImg>` extraction in `docs/frontend/admin-reference-platforms.md`.

- [ ] **Step 6: Update INDEX.txt**

Add 2 new entries (canonical tags):

```
decisions/why-ws-auth-now-required.md|decision|/ws now requires session cookie; PII over the socket forced the fix
frontend/chip-img-component.md|frontend|shared reference-chip thumbnail with onError fallback
```

- [ ] **Step 7: Commit**

```bash
git add docs/decisions/why-ws-auth-now-required.md docs/frontend/chip-img-component.md docs/backend-routers/reference-platforms-router.md docs/frontend/identification-panel.md docs/frontend/admin-reference-platforms.md docs/INDEX.txt
git commit -m "docs(reference-db): WS-auth decision + ChipImg doc + router and panel updates"
```

---

## Task 8 — Final verification

**Files:** none modified.

- [ ] **Step 1: Backend test suite**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py tests/test_reference_platform_baker.py tests/test_reference_platform_auto_identify.py tests/test_reference_platforms_router.py tests/test_reference_chip_image_route.py tests/test_reference_platforms_ws_events.py tests/test_pgvector_pool_registration.py tests/test_object_details.py 2>&1 | tail -3"
```

Expected: 50 passed (45 prior + 5 new ws_events).

- [ ] **Step 2: Frontend build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build.

- [ ] **Step 3: Live two-window sanity test (optional, manual)**

Open the Map workspace in two browser windows side-by-side, select the same detection in both. Approve a candidate in window A. Window B's IdentificationPanel should refresh within ~100ms.

If running two windows isn't practical, the Task 3 integration tests cover the publish path; the receive path uses the existing battle-tested `useEventStream` hook.

- [ ] **Step 4: Scope check**

```bash
# Plan F's first commit is the WS auth fix. Find it dynamically:
git log --format='%h %s' e15f34c96d54bcb0c1bd1e1a7df3f43dcaae0e29..HEAD | head -25
echo "---"
git diff --name-only e15f34c96d54bcb0c1bd1e1a7df3f43dcaae0e29..HEAD | sort
```

Expected files (~14):
- `backend/routers/ws.py` (Task 1)
- `backend/routers/reference_platforms.py` (Tasks 2, 6)
- `backend/schemas.py` (Task 6)
- `backend/tests/test_reference_platforms_ws_events.py` (Task 3, new)
- `frontend/src/components/ChipImg.tsx` (Task 4, new)
- `frontend/src/components/map/IdentificationPanel.tsx` (Tasks 4, 5)
- `frontend/src/components/admin/ReferencePlatformsView.tsx` (Tasks 4, 6)
- `docs/decisions/why-ws-auth-now-required.md` (Task 7, new)
- `docs/frontend/chip-img-component.md` (Task 7, new)
- `docs/backend-routers/reference-platforms-router.md` (Task 7)
- `docs/frontend/identification-panel.md` (Task 7)
- `docs/frontend/admin-reference-platforms.md` (Task 7)
- `docs/INDEX.txt` (Task 7)
- `docs/superpowers/plans/2026-05-27-reference-db-plan-f-websocket-sync.md` (the plan itself — likely untracked unless explicitly committed)

NOTHING in `inference-sam3/`, `backend/worker_legacy.py`.

## Definition of Done

- `/ws` endpoint rejects unauthenticated connections with code 1008; valid sessions accepted.
- Approve / Reject / Identify routes publish `identification_approved` / `identification_rejected` / `identification_refreshed` events to the `"identifications"` topic.
- 5 new integration tests pass; full reference-DB suite is 50 green.
- IdentificationPanel subscribes via `useEventStream` and re-fetches on events for the current detection_id.
- ReferencePlatformsView shows "Showing N of M" when `total > platforms.length`.
- Both chip-image sites use the new `<ChipImg>` component.
- WS-auth decision doc + ChipImg module doc + INDEX entries committed.
- No `inference-sam3/`, no `worker_legacy.py` modified.

## What this plan does NOT do

- Per-topic WS authorization (e.g. only admins can subscribe to `training:*`) — flagged for future plan.
- Rate-limiting WS reconnects — future plan if reconnect storms become an issue.
- WebSocket events from the WORKER auto-identify path — explicitly out of scope. Auto path events would require coupling pubsub to transaction commit, which is fragile. Multi-analyst sync flows through the analyst routes only.
- New tour step for the admin section — the existing tour scopes to Map workspace; adding an Admin section is a separate decision (Plan G material).
- Pagination UI ("Next / Prev page") — Plan F's "Showing N of M" is informational only. Real pagination is a follow-up.

Hand back to the user when "Definition of Done" is fully checked.
