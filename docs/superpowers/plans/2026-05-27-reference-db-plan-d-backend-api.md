# Reference Embedding DB — Plan D: Backend API + Plan C Carry-Forwards

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the Reference Embedding DB to HTTP — analyst-driven re-identify, candidate read + approve + reject, paginated platforms browse — so a future Plan E frontend has a working backend to call. Plus the four Plan C carry-forwards (helper refactor for auto+analyst code reuse, `REFERENCE_ID_AUTO_THRESHOLD` docs, `view_domain` param plumbing, structured end-to-end test of the worker splice).

**End state:** Six new HTTP routes under `/api/reference-platforms/*` and `/api/detections/{id}/identify` family. All write paths capture the authenticated analyst's username for audit (`platform_identification_candidates.reviewed_by`, `object_details.updated_by`, `object_details.platform_source='analyst'` after approval). The auto-path (Plan C) and analyst-path (Plan D) share the same `_upsert_platform_identification` helper. `REFERENCE_ID_AUTO_THRESHOLD` is discoverable via `.env.example` + the env-vars reference doc.

**Tech Stack:**
- Existing FastAPI app at `backend/main.py` — new router mounts alphabetically between `reports_router` and `system_router` at lines 196-213.
- Existing `Depends(get_current_user) → SessionUser` from `backend/auth.py:401-424` for authentication + `reviewed_by` capture.
- Existing Pydantic schema module `backend/schemas.py` — extend with Plan D request/response models.
- Existing `_VectorAwareConnection` pool from Plan B Task 1 — every cursor handed out by `postgis_db.get_cursor()` is vector-aware.
- Existing `find_similar_platforms` + `attach_identification_candidates` helpers from Plan C.
- Existing detection-target-candidates approve/reject pattern at `backend/main.py:1988-2073` as the architectural sibling.

**Parent specs (in-repo):**
- Plan A: `docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md`
- Plan B: `docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md`
- Plan C: `docs/superpowers/plans/2026-05-27-reference-db-plan-c-auto-identify.md`

---

## File Structure

**Created:**
- `backend/routers/reference_platforms.py` *(new)* — 6 endpoints, ~250 lines.
- `backend/tests/test_reference_platforms_router.py` *(new)* — integration tests covering all 6 endpoints + auth.
- `docs/backend-routers/reference-platforms-router.md` *(new)* — router module doc.
- `docs/decisions/why-analyst-username-from-session.md` *(new)* — decision: capture `reviewed_by` from `SessionUser` not request body (deviation from the detection-target-candidates pattern).

**Modified:**
- `backend/reference_platform_db.py` — extract `_upsert_platform_identification(cursor, *, detection_id, platform_name, platform_family, platform_confidence, platform_source)` helper; `attach_identification_candidates` calls it for the auto path; the new approve endpoint calls it for the analyst path.
- `backend/schemas.py` — new Pydantic models for the 6 routes' request/response shapes.
- `backend/main.py` — mount the new router at the alphabetical slot (after `reports_router`, before `system_router`).
- `.env.example` — add `REFERENCE_ID_AUTO_THRESHOLD=0.85` with a one-line comment.
- `docs/deployment/environment-variables-reference.md` — add a row for `REFERENCE_ID_AUTO_THRESHOLD`.
- `docs/decisions/why-auto-write-with-threshold.md` (Plan C) — append a one-line note that the env var is read at worker process start (requires restart to take effect).
- `docs/backend/reference-platform-db.md` — add `_upsert_platform_identification` to Key symbols.
- `docs/INDEX.txt` — two new entries (router doc + decision doc).

**Untouched in Plan D:**
- `frontend/*` — Plan E scope.
- `inference-sam3/*` — no inference changes.
- Existing routers, the worker splice — no behavior changes (just the helper extraction Task 1 will validate via the existing test suite).

---

## Task 1 — Plan C carry-forwards: env-var docs + `_upsert_platform_identification` helper extraction

**Files:**
- Modify: `backend/reference_platform_db.py`
- Modify: `.env.example`
- Modify: `docs/deployment/environment-variables-reference.md`
- Modify: `docs/decisions/why-auto-write-with-threshold.md`

The refactor is mechanical and unblocks Task 4 (the analyst approve endpoint needs the same `object_details` UPSERT SQL that `attach_identification_candidates` currently inlines). Doing it first keeps both paths sharing one SQL site.

- [ ] **Step 1: Extract `_upsert_platform_identification`**

Open `/nvme/osint/backend/reference_platform_db.py`. Find the `attach_identification_candidates` function. Inside it, the auto-apply block currently executes a `cursor.execute("""INSERT INTO object_details ...""")` (the block immediately after `if auto_applied:`). Extract that SQL into a new module-level helper just before `attach_identification_candidates`:

```python
def _upsert_platform_identification(
    cursor,
    *,
    detection_id: int,
    platform_name: str,
    platform_family: Optional[str],
    platform_confidence: float,
    platform_source: str,
    updated_by: str,
) -> None:
    """Write the four platform_* columns to object_details for `detection_id`.

    Shared by both the auto path (`attach_identification_candidates`,
    `platform_source='auto'`, `updated_by='reference-db-auto-identify'`)
    and the analyst-approve path (Plan D router, `platform_source='analyst'`,
    `updated_by=<session-username>`).

    Touches ONLY the four platform_* columns + housekeeping; analyst-asserted
    columns (threat_level, affiliation, designation, notes, etc.) are
    preserved by ON CONFLICT DO UPDATE SET semantics — unlisted columns
    survive. Intentional contract — see
    docs/decisions/why-auto-write-with-threshold.md.
    """
    if platform_source not in ("auto", "analyst", "manual"):
        raise ValueError(
            f"platform_source must be 'auto'|'analyst'|'manual', got {platform_source!r}"
        )
    cursor.execute(
        """
        INSERT INTO object_details
            (source, source_id, platform_name, platform_family,
             platform_confidence, platform_source, updated_by)
        VALUES ('detection', %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_id) DO UPDATE SET
            platform_name        = EXCLUDED.platform_name,
            platform_family      = EXCLUDED.platform_family,
            platform_confidence  = EXCLUDED.platform_confidence,
            platform_source      = EXCLUDED.platform_source,
            updated_at           = NOW(),
            updated_by           = EXCLUDED.updated_by
        """,
        (
            str(detection_id),
            platform_name,
            platform_family,
            float(platform_confidence),
            platform_source,
            updated_by,
        ),
    )
```

Then update `attach_identification_candidates` to call it. Find the auto-apply block (the one starting `if auto_applied:` and containing the inline INSERT) and replace its inline SQL with:

```python
    # Auto-apply to object_details only when top-1 cleared the threshold.
    if auto_applied:
        top = candidates[0]
        _upsert_platform_identification(
            cursor,
            detection_id=detection_id,
            platform_name=top["platform_name"],
            platform_family=top["platform_family"],
            platform_confidence=float(top["score"]),
            platform_source="auto",
            updated_by="reference-db-auto-identify",
        )
```

The block's comment that documents the "intentional overwrite" policy now lives on the helper's docstring, not at the call site. Update or remove the `# Intentional: plain EXCLUDED ...` comment at the call site so it doesn't duplicate the docstring — a one-line `# See _upsert_platform_identification` is enough.

Add `from typing import Optional` to the imports if not already present (it should be — Plan B Task 4 added it).

- [ ] **Step 2: Run the test suite — must still be 8 passed on auto-identify**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_auto_identify.py -v 2>&1 | tail -12"
```

Expected: 8 passed (same as Plan C's final state — the refactor must be behavior-preserving). If any test fails, the extraction broke something.

- [ ] **Step 3: Update `.env.example`**

Find the existing block of env-var declarations (probably near the bottom or grouped by subsystem). Add:

```
# Reference Embedding DB auto-identify threshold (cosine score in [0,1]).
# Top-1 candidate above this threshold is auto-applied to object_details.
# Read at worker process start — requires `docker compose restart worker` to take effect.
REFERENCE_ID_AUTO_THRESHOLD=0.85
```

Place it near other ML/inference threshold vars (look for `GLOBAL_CONFIDENCE_FLOOR` or similar — group by subsystem).

- [ ] **Step 4: Update `docs/deployment/environment-variables-reference.md`**

Read the existing format. Add a row for `REFERENCE_ID_AUTO_THRESHOLD` in the appropriate section (likely under "Inference" or "Reference DB"). Match the existing table or list shape — usually `var | default | description`.

- [ ] **Step 5: Append a one-line note to `docs/decisions/why-auto-write-with-threshold.md`**

In the "Threshold is operator-tunable" bullet (under "## Why"), append: `Note: read at worker process start; a `.env` edit requires `docker compose restart worker` to take effect.`

- [ ] **Step 6: Commit**

```bash
git add backend/reference_platform_db.py .env.example docs/deployment/environment-variables-reference.md docs/decisions/why-auto-write-with-threshold.md
git commit -m "refactor(reference-db): extract _upsert_platform_identification; doc REFERENCE_ID_AUTO_THRESHOLD"
```

---

## Task 2 — Pydantic models in `backend/schemas.py`

**Files:**
- Modify: `backend/schemas.py`

Add models for the 6 endpoints' request/response shapes. Keep them grouped together with a section comment so future readers see them as a unit.

- [ ] **Step 1: Add the models near the existing ObjectDetailsBody section**

Open `/nvme/osint/backend/schemas.py` and append (or place near the existing `ObjectDetailsBody`) these models. `from typing import Optional, List` if not already imported.

```python
# ---------------------------------------------------------------------------
# Reference Embedding DB — Plan D HTTP request/response models.
# Routes are mounted at backend/routers/reference_platforms.py.
# ---------------------------------------------------------------------------


class ReferenceChipRef(BaseModel):
    id: str
    chip_path: str
    source_dataset: str
    source_url: Optional[str] = None
    license_spdx: str
    attribution: Optional[str] = None


class ReferencePlatformSummary(BaseModel):
    """List-view shape — chips omitted for payload size."""
    id: str
    platform_name: str
    platform_family: str
    ontology_object_id: Optional[str] = None
    country_of_origin: Optional[str] = None
    role: Optional[str] = None
    view_domains: List[str]
    attributes: dict = {}


class ReferencePlatformDetail(ReferencePlatformSummary):
    """Detail-view shape — includes a sample of chips."""
    chips: List[ReferenceChipRef] = []


class ReferencePlatformsList(BaseModel):
    platforms: List[ReferencePlatformSummary]
    count: int


class IdentifyRequest(BaseModel):
    """Body for POST /api/detections/{id}/identify."""
    view_domain: str = "overhead"
    top_k: int = 3
    top_chips_per_platform: int = 3


class IdentificationCandidate(BaseModel):
    id: str
    detection_id: int
    platform_id: str
    platform_name: str
    platform_family: str
    score: float
    rank: int
    matched_chip_ids: List[str] = []
    status: str  # 'pending' | 'approved' | 'rejected' | 'auto_applied'
    applied_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: str


class IdentifyResponse(BaseModel):
    """POST /api/detections/{id}/identify — what the route returns."""
    detection_id: int
    candidates_written: int
    candidates: List[IdentificationCandidate]


class IdentificationCandidatesList(BaseModel):
    """GET /api/detections/{id}/identification-candidates."""
    detection_id: int
    candidates: List[IdentificationCandidate]
    count: int


class ApproveRejectResponse(BaseModel):
    """POST .../approve and .../reject."""
    candidate_id: str
    status: str  # 'approved' or 'rejected'
    detection_id: int
    platform_id: str
    reviewed_by: str
    reviewed_at: str
```

`BaseModel` should already be imported (`from pydantic import BaseModel`); confirm and add if missing.

- [ ] **Step 2: Sanity-check import**

```bash
docker compose exec -T backend bash -lc "cd /app && python -c 'from schemas import (
    ReferenceChipRef, ReferencePlatformSummary, ReferencePlatformDetail,
    ReferencePlatformsList, IdentifyRequest, IdentificationCandidate,
    IdentifyResponse, IdentificationCandidatesList, ApproveRejectResponse,
); print(\"all 9 models importable\")'"
```

Expected: `all 9 models importable`.

- [ ] **Step 3: Commit**

```bash
git add backend/schemas.py
git commit -m "schemas(reference-db): Plan D HTTP request/response models for reference-platforms router"
```

---

## Task 3 — Failing integration test for the router

**Files:**
- Create: `backend/tests/test_reference_platforms_router.py`

TDD: write the test first. Tests use FastAPI's `TestClient` against the live app (matches the `test_object_details.py` pattern).

- [ ] **Step 1: Write the test file**

Create `/nvme/osint/backend/tests/test_reference_platforms_router.py` with this content:

```python
"""Integration tests for backend/routers/reference_platforms.py.

Covers all 6 routes:
  - GET  /api/reference-platforms
  - GET  /api/reference-platforms/{platform_id}
  - POST /api/detections/{detection_id}/identify
  - GET  /api/detections/{detection_id}/identification-candidates
  - POST /api/identification-candidates/{candidate_id}/approve
  - POST /api/identification-candidates/{candidate_id}/reject

Auth: all routes require a logged-in session.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
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
    import main  # noqa: WPS433
    return TestClient(main.app)


def _login(client) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text


def _cleanup():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM platform_identification_candidates
             WHERE detection_id IN (SELECT id FROM detections WHERE class LIKE 'pytest-router-%')
        """)
        cur.execute("""
            DELETE FROM object_details
             WHERE source = 'detection'
               AND source_id IN (SELECT id::text FROM detections WHERE class LIKE 'pytest-router-%')
        """)
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-router-%'")
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-router-fixture'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-router-%'")


@pytest.fixture(scope="module")
def populated_ref():
    """Two reference platforms (Red all-ones, Blue all-neg-ones) for the tests."""
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import (
        upsert_reference_platform,
        insert_reference_chip,
        recompute_platform_centroids,
    )
    from database import postgis_db
    ensure_reference_platform_tables()
    _cleanup()
    with postgis_db.get_cursor(commit=True) as cur:
        pid_red = upsert_reference_platform(
            cur, platform_name="pytest-router-Red", platform_family="RouterRedFam",
            country_of_origin="USA", role="Test platform Red",
        )
        pid_blue = upsert_reference_platform(
            cur, platform_name="pytest-router-Blue", platform_family="RouterBlueFam",
        )
        for i in range(3):
            insert_reference_chip(
                cur,
                platform_id=pid_red, view_domain="overhead",
                source_dataset="pytest-router-fixture",
                chip_path=f"/tmp/pytest-router-red-{i}.png",
                embedding=np.full(1024, 1.0, dtype=np.float32),
                license_spdx="CC0-1.0",
            )
            insert_reference_chip(
                cur,
                platform_id=pid_blue, view_domain="overhead",
                source_dataset="pytest-router-fixture",
                chip_path=f"/tmp/pytest-router-blue-{i}.png",
                embedding=np.full(1024, -1.0, dtype=np.float32),
                license_spdx="CC0-1.0",
            )
        recompute_platform_centroids(cur, platform_id=pid_red)
        recompute_platform_centroids(cur, platform_id=pid_blue)
    yield {"red_id": pid_red, "blue_id": pid_blue}
    _cleanup()


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


# --- Auth gate ----------------------------------------------------------------


def test_list_platforms_requires_auth(client):
    resp = client.get("/api/reference-platforms")
    assert resp.status_code == 401


def test_identify_requires_auth(client):
    det_id = _insert_fake_detection("pytest-router-noauth")
    resp = client.post(
        f"/api/detections/{det_id}/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 401


# --- GET /api/reference-platforms ---------------------------------------------


def test_list_platforms_returns_seeded_rows(client, populated_ref):
    _login(client)
    resp = client.get("/api/reference-platforms?limit=200")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "platforms" in body and "count" in body
    fixture_names = {p["platform_name"] for p in body["platforms"]
                     if p["platform_name"].startswith("pytest-router-")}
    assert fixture_names == {"pytest-router-Red", "pytest-router-Blue"}


def test_list_platforms_supports_family_filter(client, populated_ref):
    _login(client)
    resp = client.get("/api/reference-platforms?family=RouterRedFam")
    assert resp.status_code == 200
    body = resp.json()
    names = [p["platform_name"] for p in body["platforms"]]
    assert "pytest-router-Red" in names
    assert "pytest-router-Blue" not in names


# --- GET /api/reference-platforms/{id} ----------------------------------------


def test_get_platform_detail_includes_chips(client, populated_ref):
    _login(client)
    pid = populated_ref["red_id"]
    resp = client.get(f"/api/reference-platforms/{pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform_name"] == "pytest-router-Red"
    assert body["platform_family"] == "RouterRedFam"
    assert body["country_of_origin"] == "USA"
    assert len(body["chips"]) >= 3
    assert body["chips"][0]["source_dataset"] == "pytest-router-fixture"
    assert body["chips"][0]["license_spdx"] == "CC0-1.0"


def test_get_platform_detail_404_for_unknown(client, populated_ref):
    _login(client)
    resp = client.get("/api/reference-platforms/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# --- POST /api/detections/{id}/identify ---------------------------------------


def test_identify_returns_ranked_candidates(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-identify")
    # Plant an embedding on the detection (mimic what inference-sam3 sets)
    from database import postgis_db
    import base64
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

    resp = client.post(
        f"/api/detections/{det_id}/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["detection_id"] == det_id
    assert body["candidates_written"] >= 1
    fixture_cands = [c for c in body["candidates"]
                     if c["platform_name"].startswith("pytest-router-")]
    assert any(c["platform_name"] == "pytest-router-Red" for c in fixture_cands), \
        "Red should be in returned candidates (matches the all-ones query)"


def test_identify_404_for_unknown_detection(client, populated_ref):
    _login(client)
    resp = client.post(
        "/api/detections/999999999/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 404


def test_identify_400_when_detection_has_no_embedding(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-no-emb")
    # Detection has metadata='{}' from _insert_fake_detection — no embedding key.
    resp = client.post(
        f"/api/detections/{det_id}/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 400, resp.text
    assert "embedding" in resp.json().get("detail", "").lower()


# --- GET /api/detections/{id}/identification-candidates -----------------------


def test_get_candidates_returns_what_identify_wrote(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-getcands")
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.full(1024, 1.0, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
    resp = client.get(f"/api/detections/{det_id}/identification-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["detection_id"] == det_id
    assert body["count"] >= 1
    # Top rank should be 1 with status either pending or auto_applied
    top = next(c for c in body["candidates"] if c["rank"] == 1)
    assert top["status"] in ("pending", "auto_applied")


# --- POST .../approve ---------------------------------------------------------


def test_approve_writes_analyst_to_object_details(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-approve")
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),  # cosine ~ 0 — below threshold
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates "
            "WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    resp = client.post(f"/api/identification-candidates/{cand_id}/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["reviewed_by"] == os.environ["ADMIN_USERNAME"]

    # object_details now reflects analyst approval
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT platform_name, platform_source, updated_by FROM object_details "
            "WHERE source = 'detection' AND source_id = %s",
            (str(det_id),),
        )
        od = cur.fetchone()
    assert od is not None
    assert od["platform_source"] == "analyst"
    assert od["updated_by"] == os.environ["ADMIN_USERNAME"]


def test_approve_404_for_unknown_candidate(client, populated_ref):
    _login(client)
    resp = client.post(
        "/api/identification-candidates/00000000-0000-0000-0000-000000000000/approve",
    )
    assert resp.status_code == 404


# --- POST .../reject ----------------------------------------------------------


def test_reject_sets_status_and_does_not_write_object_details(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-reject")
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates "
            "WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    resp = client.post(f"/api/identification-candidates/{cand_id}/reject")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["reviewed_by"] == os.environ["ADMIN_USERNAME"]

    # object_details NOT written
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT platform_name FROM object_details "
            "WHERE source = 'detection' AND source_id = %s",
            (str(det_id),),
        )
        od = cur.fetchone()
    assert od is None or od.get("platform_name") is None
```

- [ ] **Step 2: Run the test file — every test must fail with a NoSuchRoute / ImportError**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platforms_router.py -v 2>&1 | tail -25"
```

Expected: all tests FAIL with 404 (route not mounted yet) or fixture errors. The exact failure modes:
- Auth-gate tests get `404` instead of `401` (route doesn't exist; FastAPI returns 404 before reaching auth middleware).
- Other tests also get 404 on the missing routes.

That's expected RED-by-design. Some tests may pass anyway (e.g. fixtures alone won't crash); count the actual fail/pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_reference_platforms_router.py
git commit -m "test(reference-db): failing integration tests for reference-platforms router"
```

---

## Task 4 — Implement the router

**Files:**
- Create: `backend/routers/reference_platforms.py`

The router exposes 6 endpoints. Auth via `Depends(get_current_user)` for write endpoints; reads are also gated (the session middleware in `main.py` covers all `/api/*` paths). `reviewed_by` comes from `user.username` — a deviation from the detection-target-candidates pattern (which reads it from the request body). See Task 6's decision doc for the rationale.

- [ ] **Step 1: Write the router file**

Create `/nvme/osint/backend/routers/reference_platforms.py` with this content:

```python
"""HTTP endpoints for the Reference Embedding DB.

See docs/backend-routers/reference-platforms-router.md for the route catalogue
and docs/backend/reference-platform-db.md for the schema this router queries.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import SessionUser, get_current_user
from database import postgis_db
from reference_platform_db import (
    _upsert_platform_identification,
    attach_identification_candidates,
    find_similar_platforms,
)
from schemas import (
    ApproveRejectResponse,
    IdentificationCandidate,
    IdentificationCandidatesList,
    IdentifyRequest,
    IdentifyResponse,
    ReferenceChipRef,
    ReferencePlatformDetail,
    ReferencePlatformSummary,
    ReferencePlatformsList,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reference-platforms"])


def _decode_embedding_anchor(emb: dict) -> Optional[list[float]]:
    """Decode metadata['embedding'] = {model, dim, fp16_b64} to float list."""
    fp16_b64 = (emb or {}).get("fp16_b64")
    if not fp16_b64:
        return None
    try:
        raw = base64.b64decode(fp16_b64)
        arr = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
        if arr.shape != (1024,):
            return None
        return arr.tolist()
    except Exception:
        return None


def _candidate_row_to_model(row: dict) -> IdentificationCandidate:
    return IdentificationCandidate(
        id=str(row["id"]),
        detection_id=row["detection_id"],
        platform_id=str(row["platform_id"]),
        platform_name=row["platform_name"],
        platform_family=row["platform_family"],
        score=float(row["score"]),
        rank=row["rank"],
        matched_chip_ids=[str(x) for x in (row["matched_chip_ids"] or [])],
        status=row["status"],
        applied_at=row["applied_at"].isoformat() if row.get("applied_at") else None,
        reviewed_by=row.get("reviewed_by"),
        reviewed_at=row["reviewed_at"].isoformat() if row.get("reviewed_at") else None,
        created_at=row["created_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /api/reference-platforms — list (paginated)
# ---------------------------------------------------------------------------


@router.get("/api/reference-platforms", response_model=ReferencePlatformsList)
def list_reference_platforms(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    family: Optional[str] = Query(None, description="Exact match on platform_family"),
    country: Optional[str] = Query(None, description="Exact match on country_of_origin"),
    ontology_object_id: Optional[str] = Query(None),
) -> ReferencePlatformsList:
    where = []
    params: list = []
    if family:
        where.append("platform_family = %s")
        params.append(family)
    if country:
        where.append("country_of_origin = %s")
        params.append(country)
    if ontology_object_id:
        where.append("ontology_object_id = %s")
        params.append(ontology_object_id)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    with postgis_db.get_cursor(commit=False) as cur:
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

    platforms = [
        ReferencePlatformSummary(
            id=r["id"],
            platform_name=r["platform_name"],
            platform_family=r["platform_family"],
            ontology_object_id=r["ontology_object_id"],
            country_of_origin=r["country_of_origin"],
            role=r["role"],
            view_domains=list(r["view_domains"] or []),
            attributes=r["attributes"] or {},
        )
        for r in rows
    ]
    return ReferencePlatformsList(platforms=platforms, count=len(platforms))


# ---------------------------------------------------------------------------
# GET /api/reference-platforms/{platform_id} — detail with chips
# ---------------------------------------------------------------------------


@router.get(
    "/api/reference-platforms/{platform_id}",
    response_model=ReferencePlatformDetail,
)
def get_reference_platform(
    platform_id: str,
    max_chips: int = Query(20, ge=1, le=100),
) -> ReferencePlatformDetail:
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id::text AS id, platform_name, platform_family,
                   ontology_object_id, country_of_origin, role,
                   view_domains, attributes
              FROM reference_platforms
             WHERE id = %s
            """,
            (platform_id,),
        )
        platform_row = cur.fetchone()
        if not platform_row:
            raise HTTPException(status_code=404, detail="reference_platform not found")
        cur.execute(
            """
            SELECT id::text AS id, chip_path, source_dataset, source_url,
                   license_spdx, attribution
              FROM reference_chips
             WHERE platform_id = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (platform_id, max_chips),
        )
        chip_rows = cur.fetchall()

    chips = [
        ReferenceChipRef(
            id=r["id"],
            chip_path=r["chip_path"],
            source_dataset=r["source_dataset"],
            source_url=r["source_url"],
            license_spdx=r["license_spdx"],
            attribution=r["attribution"],
        )
        for r in chip_rows
    ]
    return ReferencePlatformDetail(
        id=platform_row["id"],
        platform_name=platform_row["platform_name"],
        platform_family=platform_row["platform_family"],
        ontology_object_id=platform_row["ontology_object_id"],
        country_of_origin=platform_row["country_of_origin"],
        role=platform_row["role"],
        view_domains=list(platform_row["view_domains"] or []),
        attributes=platform_row["attributes"] or {},
        chips=chips,
    )


# ---------------------------------------------------------------------------
# POST /api/detections/{detection_id}/identify — re-run lookup
# ---------------------------------------------------------------------------


@router.post(
    "/api/detections/{detection_id}/identify",
    response_model=IdentifyResponse,
)
def identify_detection(
    detection_id: int,
    body: IdentifyRequest,
) -> IdentifyResponse:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT metadata FROM detections WHERE id = %s",
            (detection_id,),
        )
        det_row = cur.fetchone()
        if not det_row:
            raise HTTPException(status_code=404, detail="detection not found")
        metadata = det_row["metadata"] or {}
        emb_dict = metadata.get("embedding") if isinstance(metadata, dict) else None
        if not emb_dict:
            raise HTTPException(
                status_code=400,
                detail="detection has no embedding (cannot identify without one)",
            )
        anchor = _decode_embedding_anchor(emb_dict)
        if anchor is None:
            raise HTTPException(
                status_code=400,
                detail="detection embedding is malformed (cannot decode fp16_b64)",
            )

        # Attach (re-writes the candidate queue idempotently)
        n = attach_identification_candidates(
            cur,
            detection_id=detection_id,
            embedding=anchor,
            view_domain=body.view_domain,
            auto_threshold=999.0,  # disable auto-apply on analyst re-runs
            top_k=body.top_k,
        )

        cur.execute(
            """
            SELECT c.id::text AS id, c.detection_id, c.platform_id::text AS platform_id,
                   p.platform_name, p.platform_family,
                   c.score, c.rank, c.matched_chip_ids::text[] AS matched_chip_ids,
                   c.status, c.applied_at, c.reviewed_by, c.reviewed_at, c.created_at
              FROM platform_identification_candidates c
              JOIN reference_platforms p ON c.platform_id = p.id
             WHERE c.detection_id = %s
             ORDER BY c.rank
            """,
            (detection_id,),
        )
        rows = cur.fetchall()

    candidates = [_candidate_row_to_model(r) for r in rows]
    return IdentifyResponse(
        detection_id=detection_id,
        candidates_written=n,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# GET /api/detections/{detection_id}/identification-candidates — read queue
# ---------------------------------------------------------------------------


@router.get(
    "/api/detections/{detection_id}/identification-candidates",
    response_model=IdentificationCandidatesList,
)
def get_identification_candidates(
    detection_id: int,
) -> IdentificationCandidatesList:
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT c.id::text AS id, c.detection_id, c.platform_id::text AS platform_id,
                   p.platform_name, p.platform_family,
                   c.score, c.rank, c.matched_chip_ids::text[] AS matched_chip_ids,
                   c.status, c.applied_at, c.reviewed_by, c.reviewed_at, c.created_at
              FROM platform_identification_candidates c
              JOIN reference_platforms p ON c.platform_id = p.id
             WHERE c.detection_id = %s
             ORDER BY c.rank
            """,
            (detection_id,),
        )
        rows = cur.fetchall()
    candidates = [_candidate_row_to_model(r) for r in rows]
    return IdentificationCandidatesList(
        detection_id=detection_id,
        candidates=candidates,
        count=len(candidates),
    )


# ---------------------------------------------------------------------------
# POST /api/identification-candidates/{candidate_id}/approve
# ---------------------------------------------------------------------------


@router.post(
    "/api/identification-candidates/{candidate_id}/approve",
    response_model=ApproveRejectResponse,
)
def approve_identification_candidate(
    candidate_id: str,
    user: SessionUser = Depends(get_current_user),
) -> ApproveRejectResponse:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE platform_identification_candidates
               SET status = 'approved',
                   reviewed_by = %s,
                   reviewed_at = NOW(),
                   applied_at = NOW()
             WHERE id = %s
            RETURNING id::text AS id, detection_id, platform_id::text AS platform_id,
                      score, reviewed_by, reviewed_at
            """,
            (user.username, candidate_id),
        )
        cand = cur.fetchone()
        if not cand:
            raise HTTPException(status_code=404, detail="candidate not found")
        # Look up platform name/family for the upsert
        cur.execute(
            "SELECT platform_name, platform_family FROM reference_platforms WHERE id = %s",
            (cand["platform_id"],),
        )
        plat = cur.fetchone()
        if not plat:
            # Defensive — the FK should make this impossible
            raise HTTPException(status_code=500, detail="referenced platform missing")
        _upsert_platform_identification(
            cur,
            detection_id=cand["detection_id"],
            platform_name=plat["platform_name"],
            platform_family=plat["platform_family"],
            platform_confidence=float(cand["score"]),
            platform_source="analyst",
            updated_by=user.username,
        )

    return ApproveRejectResponse(
        candidate_id=cand["id"],
        status="approved",
        detection_id=cand["detection_id"],
        platform_id=cand["platform_id"],
        reviewed_by=cand["reviewed_by"],
        reviewed_at=cand["reviewed_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /api/identification-candidates/{candidate_id}/reject
# ---------------------------------------------------------------------------


@router.post(
    "/api/identification-candidates/{candidate_id}/reject",
    response_model=ApproveRejectResponse,
)
def reject_identification_candidate(
    candidate_id: str,
    user: SessionUser = Depends(get_current_user),
) -> ApproveRejectResponse:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE platform_identification_candidates
               SET status = 'rejected',
                   reviewed_by = %s,
                   reviewed_at = NOW()
             WHERE id = %s
            RETURNING id::text AS id, detection_id, platform_id::text AS platform_id,
                      reviewed_by, reviewed_at
            """,
            (user.username, candidate_id),
        )
        cand = cur.fetchone()
        if not cand:
            raise HTTPException(status_code=404, detail="candidate not found")
    return ApproveRejectResponse(
        candidate_id=cand["id"],
        status="rejected",
        detection_id=cand["detection_id"],
        platform_id=cand["platform_id"],
        reviewed_by=cand["reviewed_by"],
        reviewed_at=cand["reviewed_at"].isoformat(),
    )
```

- [ ] **Step 2: Run the router test file — should still mostly fail (router not mounted yet)**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platforms_router.py -v 2>&1 | tail -25"
```

Expected: still mostly failing because the router isn't mounted yet. Task 5 mounts it. The tests pass on Task 5.

- [ ] **Step 3: Commit**

```bash
git add backend/routers/reference_platforms.py
git commit -m "feat(reference-db): reference-platforms router with 6 endpoints"
```

---

## Task 5 — Mount the router in `main.py`

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add the import and include_router call**

Open `/nvme/osint/backend/main.py` and find the router import block around lines 196-213 (the `from routers import … as _<name>_router` lines). Add the new import alphabetically (between `reports` and `system`):

```python
from routers import reference_platforms as _reference_platforms_router
```

Then find the matching `app.include_router(...)` block and add:

```python
app.include_router(_reference_platforms_router.router)
```

in the corresponding alphabetical position.

- [ ] **Step 2: Run the full router test file — must show all 12 tests passing**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platforms_router.py -v 2>&1 | tail -20"
```

Expected: 12 passed.

- [ ] **Step 3: Run the full reference-DB suite — must show 39 passed**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py tests/test_reference_platform_baker.py tests/test_reference_platform_auto_identify.py tests/test_reference_platforms_router.py tests/test_pgvector_pool_registration.py tests/test_object_details.py -v 2>&1 | tail -3"
```

Expected: 39 passed (8 + 5 + 8 + 12 + 2 + 4).

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "feat(reference-db): mount reference-platforms router at /api/reference-platforms"
```

---

## Task 6 — Documentation

**Files:**
- Create: `docs/backend-routers/reference-platforms-router.md`
- Create: `docs/decisions/why-analyst-username-from-session.md`
- Modify: `docs/backend/reference-platform-db.md` (mention `_upsert_platform_identification` in Key symbols)
- Modify: `docs/INDEX.txt`

- [ ] **Step 1: Write the router module doc**

Create `/nvme/osint/docs/backend-routers/reference-platforms-router.md`:

```markdown
# `backend/routers/reference_platforms.py` — Reference Embedding DB HTTP API

**Path:** [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py)
**Lines:** ~250
**Depends on:** `backend/reference_platform_db.py` (helpers), `backend/schemas.py` (Pydantic models), `backend/auth.py` (`get_current_user`), `backend/database.py` (pool).

## Purpose
Exposes the Reference Embedding DB to authenticated analysts. Six routes:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/reference-platforms` | List platforms; filter by family/country/ontology_object_id; paginated. |
| GET | `/api/reference-platforms/{platform_id}` | Detail view with up to `max_chips` reference chips. 404 if unknown. |
| POST | `/api/detections/{detection_id}/identify` | Re-run lookup against pgvector for an existing detection that has an embedding. Re-writes the candidate queue idempotently; never auto-applies (analyst path). |
| GET | `/api/detections/{detection_id}/identification-candidates` | Read the current candidate queue for a detection. |
| POST | `/api/identification-candidates/{candidate_id}/approve` | Set status='approved', write `platform_*` to `object_details` with `platform_source='analyst'`, `updated_by=<session-username>`. |
| POST | `/api/identification-candidates/{candidate_id}/reject` | Set status='rejected'; leaves `object_details` untouched. |

## Why this design
- **Reuses the existing session middleware** for auth — every `/api/*` route is gated. Write endpoints take `Depends(get_current_user)` explicitly to capture `user.username` for `reviewed_by`. See [why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md).
- **`/identify` disables auto-apply** by passing `auto_threshold=999.0`. Analysts already saw the original auto-applied candidate; the re-identify is meant to surface alternatives, not silently rewrite `object_details` again. Approve/reject is the analyst's path to write.
- **Approve and the worker auto-path share `_upsert_platform_identification`** — one SQL site for both, differs only by `platform_source` and `updated_by`. Decision: [why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md).
- **`view_domain` is per-request**, defaulting to `"overhead"`. Plan C's worker splice was overhead-only by design (every satellite detection is overhead); Plan D's request body lets analysts identify FMV ground-view chips when that path lands.

## Key symbols
- [`list_reference_platforms`](../../backend/routers/reference_platforms.py) — GET list with filters.
- [`get_reference_platform`](../../backend/routers/reference_platforms.py) — GET detail with chips.
- [`identify_detection`](../../backend/routers/reference_platforms.py) — POST analyst re-identify.
- [`get_identification_candidates`](../../backend/routers/reference_platforms.py) — GET queue.
- [`approve_identification_candidate`](../../backend/routers/reference_platforms.py) — POST analyst approve (also writes platform_* to object_details).
- [`reject_identification_candidate`](../../backend/routers/reference_platforms.py) — POST analyst reject.
- `_decode_embedding_anchor` — local helper that decodes `metadata['embedding'].fp16_b64` to a float list.

## Inputs / Outputs
- Inputs: HTTP requests with a valid `sentinel_session` cookie. Path/query/body Pydantic-validated.
- Outputs: JSON per the Pydantic response models in `backend/schemas.py`.

## Failure modes
- 401 Unauthorized — no valid session.
- 400 — detection has no embedding (cannot identify without one).
- 404 — detection / platform / candidate not found.
- 500 — defensive; only fires on FK violations the schema should already prevent.

## Cross-references
- Schema and helpers: [reference-platform-db.md](../backend/reference-platform-db.md).
- Auto-identify worker path (sibling): [reference-platform-baker.md](../backend/reference-platform-baker.md) (the baker is for write; the auto path is in `worker-legacy-monolith.md`).
- Plan D spec (in-repo): [docs/superpowers/plans/2026-05-27-reference-db-plan-d-backend-api.md](../superpowers/plans/2026-05-27-reference-db-plan-d-backend-api.md).
```

- [ ] **Step 2: Write the decision doc**

Create `/nvme/osint/docs/decisions/why-analyst-username-from-session.md`:

```markdown
**Decision:** Plan D's approve/reject endpoints capture `reviewed_by` from `Depends(get_current_user).username`, NOT from a request-body `analyst` field. This deviates from the existing `detection_target_candidates` pattern at `backend/main.py:1988-2073`, which reads the analyst name from the request body.

## Why
- **The session cookie is the source of truth for who's logged in.** Trusting a request-body `analyst` field means a malicious or buggy client could submit any name. The session-derived username is signed and cannot be spoofed without an active session.
- **`detection_target_candidates`'s pattern predates the project's auth posture being analyst-centric.** Plan D is the right opportunity to establish the better convention for new code; the old pattern can be migrated later as a separate hygiene task.
- **Simpler clients.** Frontend code in Plan E does not need to fetch and pass `user.username` — it just POSTs and the backend resolves the username from the cookie.

## What we rejected
- **Request-body `analyst` field** — matches the existing pattern but encodes user identity client-side. Rejected for the reason above.
- **A separate audit log table** — premature. The candidate row's `reviewed_by`/`reviewed_at` columns are sufficient for now; a richer audit trail can be added if compliance requirements escalate.

## Consequences
- Plan D's approve/reject routes require `Depends(get_current_user)` explicitly.
- The session must be valid; a 401 is returned otherwise (handled by the existing middleware).
- The decision is decoupled from the existing `detection_target_candidates` pattern, which keeps its current behaviour. A future migration could harmonise.
```

- [ ] **Step 3: Update `docs/backend/reference-platform-db.md`**

Add a Key symbols bullet for `_upsert_platform_identification` between the existing `attach_identification_candidates` and `find_similar_platforms` entries (or wherever fits the existing list ordering). Use `grep -n "^def _upsert_platform_identification" backend/reference_platform_db.py` for the line range.

- [ ] **Step 4: Update `docs/INDEX.txt`**

Add two entries in alphabetical position. Tags from canonical vocabulary only.

```
backend-routers/reference-platforms-router.md|router,reference-db|6 endpoints — list/detail/identify/get-candidates/approve/reject
```

Wait — `reference-db` is NOT in the canonical vocab. Use just `router`:

```
backend-routers/reference-platforms-router.md|router|6 endpoints for the Reference Embedding DB (list, detail, identify, approve/reject)
```

And for the decision doc:

```
decisions/why-analyst-username-from-session.md|decision|capture reviewed_by from SessionUser, not request-body analyst field
```

Place each in correct within-section alphabetical position.

- [ ] **Step 5: Commit**

```bash
git add docs/backend-routers/reference-platforms-router.md docs/decisions/why-analyst-username-from-session.md docs/backend/reference-platform-db.md docs/INDEX.txt
git commit -m "docs(reference-db): router doc + analyst-username decision + INDEX entries"
```

---

## Task 7 — Final end-to-end verification

**Files:** none modified.

- [ ] **Step 1: Full reference-DB suite**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py tests/test_reference_platform_baker.py tests/test_reference_platform_auto_identify.py tests/test_reference_platforms_router.py tests/test_pgvector_pool_registration.py tests/test_object_details.py 2>&1 | tail -3"
```

Expected: 39 passed.

- [ ] **Step 2: Live curl exercise — every route returns the right shape**

```bash
# Log in to get a session cookie
docker compose exec -T backend bash -lc 'curl -s -c /tmp/sess.txt -X POST -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"$(grep ^ADMIN_PASSWORD /app/.env | cut -d= -f2)\"}" http://localhost:8080/api/auth/login | head'

# List platforms
docker compose exec -T backend bash -lc 'curl -s -b /tmp/sess.txt http://localhost:8080/api/reference-platforms?limit=5 | python -m json.tool | head -30'

# Get a platform detail (any DOTA::* should exist from Plan B Task 7)
docker compose exec -T backend bash -lc '
PID=$(curl -s -b /tmp/sess.txt "http://localhost:8080/api/reference-platforms?limit=200" | python -c "import sys,json; d=json.load(sys.stdin); print([p[\"id\"] for p in d[\"platforms\"] if p[\"platform_name\"]==\"DOTA::plane\"][0])")
curl -s -b /tmp/sess.txt "http://localhost:8080/api/reference-platforms/$PID" | python -m json.tool | head -30
'
```

Expected: clean JSON responses, no 500s, the DOTA::plane platform shows its chips.

- [ ] **Step 3: Scope check**

```bash
# Plan D's first commit is Task 1's refactor commit. Find it dynamically:
git log --format='%h %s' aa2005a20bfe246d8d1e77fc68f52079e8161fbc..HEAD | head -25
git diff --name-only $(git log --format='%H' --grep='refactor(reference-db): extract _upsert_platform_identification' -1)..HEAD
```

Expected file list (Plan D scope, ~13 files):
- `backend/main.py`
- `backend/reference_platform_db.py`
- `backend/schemas.py`
- `backend/routers/reference_platforms.py`
- `backend/tests/test_reference_platforms_router.py`
- `.env.example`
- `docs/backend-routers/reference-platforms-router.md`
- `docs/decisions/why-analyst-username-from-session.md`
- `docs/decisions/why-auto-write-with-threshold.md` (Task 1 note)
- `docs/deployment/environment-variables-reference.md`
- `docs/backend/reference-platform-db.md`
- `docs/INDEX.txt`
- `docs/superpowers/plans/2026-05-27-reference-db-plan-d-backend-api.md` (the plan itself)

NOTHING in `inference-sam3/`, `frontend/`, or `backend/worker_legacy.py`.

## Definition of Done

- Six new endpoints under `/api/reference-platforms` and `/api/detections/{id}/identify` family return correct shapes.
- All 12 router tests pass; full reference-DB suite is 39 green.
- `_upsert_platform_identification` is extracted and shared by both auto and analyst paths; existing 8 auto-identify tests still pass.
- `REFERENCE_ID_AUTO_THRESHOLD` is documented in `.env.example` + the env-vars reference doc.
- Two new docs (router + decision) committed; INDEX updated; canonical tags only.
- No frontend / inference-sam3 / worker code modified.

## What this plan does NOT do

- Frontend `IdentificationPanel.tsx` component (Plan E).
- Admin `ReferencePlatformsView.tsx` tab (Plan E).
- Product Tour steps (Plan E).
- WebSocket events for live candidate updates — not needed for the analyst flow yet; can be added when concurrent-user concerns surface.
- Bulk approve/reject — single-candidate per call; bulk is a future enhancement.
- Re-identify-all background task — a maintenance plan, separate.

Hand back to the user when "Definition of Done" is fully checked.
