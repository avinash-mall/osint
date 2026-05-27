# Reference Embedding DB — Plan E: Frontend UI + Chip-Serving Route

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Analysts can see and act on platform identifications from the React UI. Specifically: every detection's SelectionPanel shows a new "Identification" section with top-k candidates (score, ref-chip thumbnail, approve/reject); the Admin screen gains a "Reference Platforms" tab to browse the curated DB; the Product Tour walks an operator through both surfaces. Plus a backend chip-serving route (Plan D hand-off) so the frontend can actually display thumbnails.

**End state:** A working end-to-end demo: open a detection in the Map workspace → see Plan C's auto-applied platform call-out and the candidate queue in the Details tab → click Approve to flip to analyst-asserted → see the value appear in ObjectDetailsForm. An operator can also open Admin → Reference Platforms and browse the 18 DOTA seed rows (or whatever is baked).

**Architecture (mirrors existing conventions):**
- **Backend**: one new GET route on the existing `reference_platforms` router that serves a chip image with path-sanitization (must be under `/data/datasets/`). No new module, no schema changes.
- **Frontend**: one new panel component (`IdentificationPanel.tsx`) inside `SelectionPanel`'s Details tab, one new admin view (`ReferencePlatformsView.tsx`), plus tour-step additions. All use axios (matches `ObjectDetailsForm`/`AlertsView` siblings); state is local `useState`+`useEffect` (no Redux/SWR — matches project convention); auth via existing session cookie.
- **ObjectDetailsForm extension**: read-only display of `platform_name`/`platform_family`/`platform_confidence`/`platform_source` when populated (Plan C/D wrote them; Plan E surfaces them). No new editor controls — analysts approve/reject via IdentificationPanel, not by typing platform names.

**Tech Stack:**
- React 19, Vite 8, TypeScript (existing).
- `axios` for HTTP (matches existing SelectionPanel-area code).
- `lucide-react` icons (matches AdminScreen `NavItemDef.Icon` pattern).
- Playwright visual tests at the end (the project's only frontend test harness).
- `useEventStream` hook is **available but not consumed** in Plan E — live multi-analyst updates are explicitly deferred (Plan D's final-review hand-off note).

**Parent specs (in-repo):**
- Plan A: `docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md`
- Plan B: `docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md`
- Plan C: `docs/superpowers/plans/2026-05-27-reference-db-plan-c-auto-identify.md`
- Plan D: `docs/superpowers/plans/2026-05-27-reference-db-plan-d-backend-api.md`

---

## File Structure

**Created:**
- `frontend/src/components/map/IdentificationPanel.tsx` *(new)* — the main UI component (~250 lines). Top-k candidates list, scores, chip thumbnails, approve/reject buttons.
- `frontend/src/components/admin/ReferencePlatformsView.tsx` *(new)* — admin tab (~150 lines). Paginated list with family/country filters; click-through to detail with chips.
- `backend/tests/test_reference_chip_image_route.py` *(new)* — integration test for the chip-serving route (auth gate, path traversal protection, 404 on unknown id).
- `docs/frontend/identification-panel.md` *(new)* — module doc for the panel component.
- `docs/frontend/admin-reference-platforms.md` *(new)* — module doc for the admin view.

**Modified:**
- `backend/routers/reference_platforms.py` — add `GET /api/reference-chips/{chip_id}/image` route + path sanitization.
- `frontend/src/components/map/SelectionPanel.tsx` — mount `IdentificationPanel` in the Details tab (between Taxonomy and Allegiance sections). Add a `data-tour="identification-panel"` attribute.
- `frontend/src/components/ObjectDetailsForm.tsx` — display the four `platform_*` fields read-only when populated. No new form controls.
- `frontend/src/components/AdminScreen.tsx` — register the new `reference-platforms` tab in the `NAV` array. Add a `data-tour="admin-reference-platforms"` attribute.
- `frontend/src/components/tour/tourSteps.ts` — add 2 new tour steps (Identification panel + Admin Reference Platforms tab).
- `docs/INDEX.txt` — two new entries.
- `docs/frontend/map-selection-panel.md` — refresh to note the new Identification subsection.
- `docs/backend-routers/reference-platforms-router.md` — refresh to add the chip-serving route to the route table.

**Untouched in Plan E:**
- `inference-sam3/*` — no inference changes.
- Any other backend router — only `reference_platforms.py` modified.
- Worker code, schema — already done in Plans A/B/C/D.

---

## Task 1 — Backend chip-serving route

**Files:**
- Modify: `backend/routers/reference_platforms.py`
- Create: `backend/tests/test_reference_chip_image_route.py`

The frontend needs to render reference-chip thumbnails. `reference_chips.chip_path` is a filesystem path like `/data/datasets/reference-chips/dota/plane/chip_5_0__plane.png`. Plan E adds one new route that:
1. Looks up `chip_path` by `chip_id`.
2. Verifies the resolved path is under `/data/datasets/` (prevents path traversal).
3. Returns the file via `FileResponse` with `image/png` content-type.

- [ ] **Step 1: Write the failing test**

Create `/nvme/osint/backend/tests/test_reference_chip_image_route.py`:

```python
"""Integration tests for GET /api/reference-chips/{chip_id}/image."""

from __future__ import annotations

import os
import sys
import uuid
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


def _login(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text


def _cleanup():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-chip-image'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-chip-image-%'")


@pytest.fixture(scope="module")
def fixture_chip(tmp_path_factory):
    """Stage a real PNG file at /data/datasets/reference-chips/pytest/...
    and insert a reference_chips row pointing at it."""
    from PIL import Image
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import upsert_reference_platform, insert_reference_chip
    from database import postgis_db
    ensure_reference_platform_tables()
    _cleanup()

    # Write a synthetic PNG inside /data/datasets/ — the only path the route allows.
    chip_dir = Path("/data/datasets/reference-chips/pytest")
    chip_dir.mkdir(parents=True, exist_ok=True)
    chip_path = chip_dir / "pytest-chip-image.png"
    Image.new("RGB", (32, 32), color=(120, 50, 80)).save(chip_path)

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-chip-image-X", platform_family="PytestFam"
        )
        chip_id = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-chip-image",
            chip_path=str(chip_path),
            embedding=np.full(1024, 0.5, dtype=np.float32),
            license_spdx="CC0-1.0",
        )
    yield {"chip_id": chip_id, "chip_path": chip_path}
    _cleanup()
    try:
        chip_path.unlink()
    except OSError:
        pass


def test_chip_image_requires_auth(client, fixture_chip):
    resp = client.get(f"/api/reference-chips/{fixture_chip['chip_id']}/image")
    assert resp.status_code == 401


def test_chip_image_returns_png_for_valid_id(client, fixture_chip):
    _login(client)
    resp = client.get(f"/api/reference-chips/{fixture_chip['chip_id']}/image")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("image/png")
    assert len(resp.content) > 0


def test_chip_image_404_for_unknown_id(client):
    _login(client)
    resp = client.get(f"/api/reference-chips/{uuid.uuid4()}/image")
    assert resp.status_code == 404


def test_chip_image_403_for_path_outside_data_datasets(client, monkeypatch):
    """A chip row whose chip_path points outside /data/datasets MUST be rejected
    even if the row exists. Guards against future bad data + path traversal."""
    _login(client)
    from database import postgis_db
    from reference_platform_db import upsert_reference_platform, insert_reference_chip
    import numpy as np
    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-chip-image-evil", platform_family="EvilFam"
        )
        # Path points outside the allowed root
        chip_id = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-chip-image",
            chip_path="/etc/passwd",
            embedding=np.full(1024, 0.0, dtype=np.float32),
            license_spdx="CC0-1.0",
        )
    resp = client.get(f"/api/reference-chips/{chip_id}/image")
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"
```

- [ ] **Step 2: Run the test — expect 404s (route doesn't exist yet)**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_chip_image_route.py -v 2>&1 | tail -10"
```

Expected: tests fail with 404 because the route doesn't exist. Some may pass coincidentally (the unknown-id test expects 404, gets 404 for "no such route" — same code).

- [ ] **Step 3: Add the route to `backend/routers/reference_platforms.py`**

Open the router file. After the existing endpoints (the `reject_identification_candidate` block at the bottom), add this route. The `from fastapi.responses import FileResponse` import needs to land at the top of the file too.

```python
# ---------------------------------------------------------------------------
# GET /api/reference-chips/{chip_id}/image — serve a chip thumbnail
# ---------------------------------------------------------------------------

# Constant root every chip_path must be under. Set at import time so any
# misconfiguration surfaces immediately rather than per-request.
_REFERENCE_CHIPS_ROOT = Path("/data/datasets").resolve()


@router.get("/api/reference-chips/{chip_id}/image")
def serve_reference_chip_image(
    chip_id: str,
    user: SessionUser = Depends(get_current_user),
):
    """Stream the chip PNG/JPEG at `reference_chips.chip_path`.

    Defense in depth: the resolved chip_path MUST be under `/data/datasets/`.
    A row pointing anywhere else (data corruption, malicious migration)
    returns 403, NOT the file. Prevents path traversal even if the DB is
    compromised.
    """
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT chip_path FROM reference_chips WHERE id = %s",
            (chip_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="reference_chip not found")

    raw_path = row["chip_path"]
    try:
        resolved = Path(raw_path).resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid chip path: {e}")

    # Validate resolved path is under the allowed root.
    try:
        resolved.relative_to(_REFERENCE_CHIPS_ROOT)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="chip_path is not under /data/datasets/ (refusing to serve)",
        )

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="chip file not on disk")

    # Infer media type from extension; default to octet-stream for unknown.
    ext = resolved.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(resolved),
        media_type=media_type,
        filename=resolved.name,
    )
```

Add the imports at the top:

```python
from pathlib import Path
from fastapi.responses import FileResponse
```

- [ ] **Step 4: Run the test — all 4 should pass**

```bash
docker compose up -d --build backend
sleep 5
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_chip_image_route.py -v 2>&1 | tail -10"
```

Expected: 4 passed.

- [ ] **Step 5: Confirm no regression on the rest of the router suite**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platforms_router.py tests/test_reference_chip_image_route.py 2>&1 | tail -3"
```

Expected: 17 passed (13 prior + 4 new).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/reference_platforms.py backend/tests/test_reference_chip_image_route.py
git commit -m "feat(reference-db): GET /api/reference-chips/{id}/image with path-traversal guard"
```

---

## Task 2 — Extend `ObjectDetailsForm` to display platform_* fields read-only

**Files:**
- Modify: `frontend/src/components/ObjectDetailsForm.tsx`

Plan C/D backend writes `platform_name`/`platform_family`/`platform_confidence`/`platform_source` to `object_details`. The current form doesn't surface them. Plan E adds a small read-only display section (no editor controls — analysts approve/reject via IdentificationPanel, not by typing).

- [ ] **Step 1: Locate the form's existing field rendering**

Read `frontend/src/components/ObjectDetailsForm.tsx`. Specifically lines 176–183 (the existing field group: designation, object_class, etc.). Find the matching TypeScript interface for the API response (look near the top of the file for an `ObjectPayload` or similar).

- [ ] **Step 2: Add the four platform_* fields to the TypeScript interface**

In the interface that mirrors `object_details` (probably `ObjectPayload` or `ObjectDetailsBody`), add:

```typescript
  platform_name?: string | null;
  platform_family?: string | null;
  platform_confidence?: number | null;
  platform_source?: string | null;  // 'auto' | 'analyst' | 'manual'
```

- [ ] **Step 3: Add a read-only display section**

After the existing field group (around line 183 where `notes` is rendered), insert a conditional section that ONLY renders when `platform_name` is set. Use the same `.row`/`.label`/`.value` markup conventions you see in the existing fields. Example:

```tsx
{form.platform_name ? (
  <div className="form-section platform-id-section" data-tour="object-details-platform">
    <div className="section-title">Platform identification</div>
    <div className="row">
      <span className="label">Platform</span>
      <span className="value">{form.platform_name}</span>
    </div>
    {form.platform_family && (
      <div className="row">
        <span className="label">Family</span>
        <span className="value">{form.platform_family}</span>
      </div>
    )}
    {form.platform_confidence != null && (
      <div className="row">
        <span className="label">Confidence</span>
        <span className="value">{(form.platform_confidence * 100).toFixed(1)}%</span>
      </div>
    )}
    {form.platform_source && (
      <div className="row">
        <span className="label">Source</span>
        <span className={`value source-${form.platform_source}`}>
          {form.platform_source === "auto" ? "Auto-identified" :
           form.platform_source === "analyst" ? "Analyst-approved" :
           form.platform_source === "manual" ? "Manually set" :
           form.platform_source}
        </span>
      </div>
    )}
  </div>
) : null}
```

Match the exact JSX patterns already in the file — don't invent new markup conventions if the file uses tables / specific class names.

- [ ] **Step 4: Build the frontend to confirm no TypeScript errors**

```bash
docker compose exec -T frontend bash -lc "cd /app && npm run build 2>&1 | tail -15"
```

If `frontend` isn't running, `cd frontend && npm run build` on the host.

Expected: clean build, no TS errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ObjectDetailsForm.tsx
git commit -m "feat(reference-db): ObjectDetailsForm read-only display of platform_* fields"
```

---

## Task 3 — Build `IdentificationPanel.tsx`

**Files:**
- Create: `frontend/src/components/map/IdentificationPanel.tsx`

This is Plan E's biggest task — the main UI surface. The panel:
1. Fetches `GET /api/detections/{detectionId}/identification-candidates` on mount + when detection changes.
2. Renders top-k candidates with: rank, platform_name, platform_family, score (as a confidence bar), one ref-chip thumbnail (from the chip-serving route).
3. Provides Approve/Reject buttons per candidate.
4. Has a "Re-identify" button that POSTs to `/api/detections/{detectionId}/identify` and refreshes the list.
5. Handles loading/error/empty states cleanly.
6. Mirrors the layout/styling of `ReviewPanel.tsx` (the architectural sibling).

- [ ] **Step 1: Sample the sibling component**

Read `frontend/src/components/map/ReviewPanel.tsx` (~200 lines). Specifically:
- The 3-button (Accept/Flag/Reject) header layout.
- The error-chip styling.
- How `axios` is imported and used.
- The fetch-on-mount pattern.

Mimic this shape — Plan E's IdentificationPanel should feel familiar to anyone who's used ReviewPanel.

- [ ] **Step 2: Write the component**

Create `/nvme/osint/frontend/src/components/map/IdentificationPanel.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Check, X, RefreshCw } from "lucide-react";

interface IdentificationCandidate {
  id: string;
  detection_id: number;
  platform_id: string;
  platform_name: string;
  platform_family: string;
  score: number;
  rank: number;
  matched_chip_ids: string[];
  status: "pending" | "approved" | "rejected" | "auto_applied";
  applied_at?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  created_at: string;
}

interface IdentificationCandidatesList {
  detection_id: number;
  candidates: IdentificationCandidate[];
  count: number;
}

interface Props {
  detectionId: number;
  /** Called after approve/reject so the parent can refresh object_details. */
  onChanged?: () => void;
}

export default function IdentificationPanel({ detectionId, onChanged }: Props) {
  const [candidates, setCandidates] = useState<IdentificationCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyCandidate, setBusyCandidate] = useState<string | null>(null);
  const [reidentifyBusy, setReidentifyBusy] = useState(false);

  async function load() {
    setError(null);
    try {
      const resp = await axios.get<IdentificationCandidatesList>(
        `/api/detections/${detectionId}/identification-candidates`,
        { withCredentials: true },
      );
      setCandidates(resp.data.candidates);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "load failed");
      setCandidates(null);
    }
  }

  useEffect(() => {
    void load();
  }, [detectionId]);

  async function handleApprove(candidateId: string) {
    setBusyCandidate(candidateId);
    setError(null);
    try {
      await axios.post(
        `/api/identification-candidates/${candidateId}/approve`,
        {},
        { withCredentials: true },
      );
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "approve failed");
    } finally {
      setBusyCandidate(null);
    }
  }

  async function handleReject(candidateId: string) {
    setBusyCandidate(candidateId);
    setError(null);
    try {
      await axios.post(
        `/api/identification-candidates/${candidateId}/reject`,
        {},
        { withCredentials: true },
      );
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "reject failed");
    } finally {
      setBusyCandidate(null);
    }
  }

  async function handleReidentify() {
    setReidentifyBusy(true);
    setError(null);
    try {
      await axios.post(
        `/api/detections/${detectionId}/identify`,
        { view_domain: "overhead", top_k: 3 },
        { withCredentials: true },
      );
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "re-identify failed");
    } finally {
      setReidentifyBusy(false);
    }
  }

  const hasCandidates = candidates && candidates.length > 0;
  const sorted = useMemo(() => {
    if (!candidates) return [];
    return [...candidates].sort((a, b) => a.rank - b.rank);
  }, [candidates]);

  return (
    <div className="identification-panel" data-tour="identification-panel">
      <div className="section-header">
        <span className="section-title">Platform identification</span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={handleReidentify}
          disabled={reidentifyBusy}
          title="Re-run lookup against reference platforms"
        >
          <RefreshCw size={14} />
          {reidentifyBusy ? "Re-identifying…" : "Re-identify"}
        </button>
      </div>

      {error && (
        <div className="error-chip mono">
          {error}
        </div>
      )}

      {candidates === null && !error && (
        <div className="muted">Loading…</div>
      )}

      {candidates !== null && !hasCandidates && (
        <div className="muted">
          No identification candidates. {" "}
          <button type="button" className="link" onClick={handleReidentify}>
            Run identify
          </button>
          {" "}to populate.
        </div>
      )}

      {hasCandidates && (
        <ul className="candidate-list">
          {sorted.map((c) => (
            <li key={c.id} className={`candidate candidate-${c.status}`}>
              <div className="candidate-row">
                <span className="candidate-rank">#{c.rank}</span>
                <span className="candidate-name">{c.platform_name}</span>
                <span className="candidate-family">{c.platform_family}</span>
                <span className="candidate-score" title={`cosine = ${c.score.toFixed(4)}`}>
                  {(c.score * 100).toFixed(1)}%
                </span>
              </div>
              <div className="candidate-chips">
                {c.matched_chip_ids.slice(0, 3).map((cid) => (
                  <img
                    key={cid}
                    src={`/api/reference-chips/${cid}/image`}
                    alt="reference chip"
                    className="chip-thumb"
                    loading="lazy"
                  />
                ))}
              </div>
              <div className="candidate-actions">
                <span className={`status-tag status-${c.status}`}>
                  {c.status === "auto_applied" ? "Auto-applied" :
                   c.status === "approved" ? "Approved" :
                   c.status === "rejected" ? "Rejected" :
                   "Pending"}
                </span>
                {c.status !== "approved" && c.status !== "rejected" && (
                  <>
                    <button
                      type="button"
                      className="btn btn-approve"
                      disabled={busyCandidate === c.id}
                      onClick={() => handleApprove(c.id)}
                      aria-label={`Approve ${c.platform_name}`}
                    >
                      <Check size={14} />
                      Approve
                    </button>
                    <button
                      type="button"
                      className="btn btn-reject"
                      disabled={busyCandidate === c.id}
                      onClick={() => handleReject(c.id)}
                      aria-label={`Reject ${c.platform_name}`}
                    >
                      <X size={14} />
                      Reject
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add minimal CSS**

Find the CSS file the rest of `SelectionPanel`-area uses (likely `frontend/src/styles/index.css` or `additions.responsive.css` — check what `ReviewPanel.tsx` imports or relies on). Add a `.identification-panel` rule block that mirrors the existing `.review-panel` styles. Keep changes minimal — the project's CSS conventions are documented in `docs/decisions/merged-additions-responsive-css.md`.

Sample CSS (adjust class names + colors to match the existing palette):

```css
.identification-panel { padding: 8px 0; }
.identification-panel .section-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 6px;
}
.identification-panel .candidate-list { list-style: none; padding: 0; margin: 0; }
.identification-panel .candidate { 
  display: grid; gap: 4px; padding: 6px;
  border: 1px solid var(--border, #2a2f38);
  border-radius: 4px; margin-bottom: 6px;
}
.identification-panel .candidate-auto_applied { border-color: var(--ok, #38c172); }
.identification-panel .candidate-approved { border-color: var(--ok, #38c172); }
.identification-panel .candidate-rejected { opacity: 0.5; }
.identification-panel .candidate-row {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px;
}
.identification-panel .candidate-rank { font-weight: bold; color: var(--muted, #777); }
.identification-panel .candidate-score { margin-left: auto; font-variant-numeric: tabular-nums; }
.identification-panel .candidate-chips {
  display: flex; gap: 4px;
}
.identification-panel .chip-thumb {
  width: 32px; height: 32px; object-fit: cover; border-radius: 2px;
  background: var(--bg-2, #1a1d23);
}
.identification-panel .candidate-actions {
  display: flex; align-items: center; gap: 6px; font-size: 11px;
}
.identification-panel .btn-approve { color: var(--ok, #38c172); }
.identification-panel .btn-reject { color: var(--nato-hostile, #c0392b); }
.identification-panel .status-tag {
  padding: 1px 6px; border-radius: 2px; font-size: 10px;
}
.identification-panel .status-auto_applied { background: var(--ok-bg, #1e3a2e); }
.identification-panel .status-pending { background: var(--bg-2, #1a1d23); }
.identification-panel .status-approved { background: var(--ok-bg, #1e3a2e); }
.identification-panel .status-rejected { background: var(--hostile-bg, #3a1e1e); }
.identification-panel .error-chip {
  color: var(--nato-hostile, #c0392b);
  border: 1px solid var(--nato-hostile, #c0392b);
  padding: 4px 6px; border-radius: 2px; font-size: 11px;
}
```

If the project's CSS classes / vars differ, adapt — the principle is "blend with siblings."

- [ ] **Step 4: Build + smoke**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build, no TS errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/map/IdentificationPanel.tsx
# Plus any CSS file you modified
git commit -m "feat(reference-db): IdentificationPanel component with approve/reject + re-identify"
```

---

## Task 4 — Wire `IdentificationPanel` into `SelectionPanel`

**Files:**
- Modify: `frontend/src/components/map/SelectionPanel.tsx`

- [ ] **Step 1: Import the new component**

Open `/nvme/osint/frontend/src/components/map/SelectionPanel.tsx`. Add the import at the top:

```tsx
import IdentificationPanel from "./IdentificationPanel";
```

- [ ] **Step 2: Mount the panel in the Details tab**

Find the Details tab structure (around lines 291–575 per the exploration). Insert `<IdentificationPanel ... />` between the Taxonomy section and the Allegiance tagging buttons (approximately after line 435, before line 456). Pass the detection id and an `onChanged` callback that triggers the existing object-details refresh (look for an existing pattern — probably a `loadObjectDetails()` function or similar).

Example insertion (adapt to actual code shape):

```tsx
{/* … Taxonomy section ends … */}

<IdentificationPanel
  detectionId={selectedDetection.id}
  onChanged={() => refreshObjectDetails?.()}
/>

{/* … Allegiance tagging buttons begin … */}
```

If `refreshObjectDetails` doesn't exist as a prop / state setter, identify what does the equivalent (a useEffect that re-fetches when something changes? A `loadObjectDetails` function?). Wire `onChanged` to it. The goal: after approve/reject lands, the `platform_*` display in ObjectDetailsForm refreshes.

- [ ] **Step 3: Add the data-tour attribute**

The component itself sets `data-tour="identification-panel"` on its outer div, so no wrapper is needed. Confirm via grep.

- [ ] **Step 4: Build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/map/SelectionPanel.tsx
git commit -m "feat(reference-db): mount IdentificationPanel in SelectionPanel Details tab"
```

---

## Task 5 — Build `ReferencePlatformsView.tsx` admin tab

**Files:**
- Create: `frontend/src/components/admin/ReferencePlatformsView.tsx`

A paginated list of reference platforms with family/country filters and a detail drawer / inline expansion showing chips. Use `AlertsView.tsx` as the template.

- [ ] **Step 1: Sample the template**

Read `/nvme/osint/frontend/src/components/admin/AlertsView.tsx` (~67 lines). Note: `ViewHeader`, `useState`+`useEffect`+`axios.get`, optional `onCount` callback to update the NAV badge.

- [ ] **Step 2: Write the component**

Create `/nvme/osint/frontend/src/components/admin/ReferencePlatformsView.tsx`:

```tsx
import { useEffect, useState } from "react";
import axios from "axios";
import { Database, Search, X } from "lucide-react";
import ViewHeader from "./ViewHeader";  // path may differ; check AlertsView's import

interface PlatformSummary {
  id: string;
  platform_name: string;
  platform_family: string;
  ontology_object_id?: string | null;
  country_of_origin?: string | null;
  role?: string | null;
  view_domains: string[];
  attributes: Record<string, unknown>;
}

interface PlatformDetail extends PlatformSummary {
  chips: Array<{
    id: string;
    chip_path: string;
    source_dataset: string;
    source_url?: string | null;
    license_spdx: string;
    attribution?: string | null;
  }>;
}

interface Props {
  onCount?: (n: number) => void;
}

export default function ReferencePlatformsView({ onCount }: Props) {
  const [platforms, setPlatforms] = useState<PlatformSummary[]>([]);
  const [familyFilter, setFamilyFilter] = useState("");
  const [countryFilter, setCountryFilter] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState<PlatformDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { limit: "200" };
      if (familyFilter.trim()) params.family = familyFilter.trim();
      if (countryFilter.trim()) params.country = countryFilter.trim();
      const resp = await axios.get<{ platforms: PlatformSummary[]; count: number }>(
        "/api/reference-platforms",
        { params, withCredentials: true },
      );
      setPlatforms(resp.data.platforms);
      onCount?.(resp.data.count);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "load failed");
      setPlatforms([]);
      onCount?.(0);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // Re-load on filter change is user-driven (Apply button) — keep the deps empty here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function openPlatform(id: string) {
    try {
      const resp = await axios.get<PlatformDetail>(
        `/api/reference-platforms/${id}`,
        { withCredentials: true },
      );
      setSelectedPlatform(resp.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? "fetch failed");
    }
  }

  return (
    <div className="admin-view reference-platforms-view" data-tour="admin-reference-platforms">
      <ViewHeader
        title="Reference platforms"
        icon={<Database size={16} />}
        actions={
          <div className="filter-row">
            <input
              type="text"
              placeholder="family"
              value={familyFilter}
              onChange={(e) => setFamilyFilter(e.target.value)}
            />
            <input
              type="text"
              placeholder="country"
              value={countryFilter}
              onChange={(e) => setCountryFilter(e.target.value)}
            />
            <button type="button" className="btn btn-sm" onClick={() => void load()}>
              <Search size={14} /> Apply
            </button>
          </div>
        }
      />

      {error && <div className="error-chip mono">{error}</div>}
      {loading && <div className="muted">Loading…</div>}

      <div className="reference-platforms-grid">
        <ul className="platform-list scroll">
          {platforms.map((p) => (
            <li
              key={p.id}
              className={`platform-row ${selectedPlatform?.id === p.id ? "selected" : ""}`}
              onClick={() => void openPlatform(p.id)}
            >
              <span className="platform-name">{p.platform_name}</span>
              <span className="platform-family muted">{p.platform_family}</span>
              {p.country_of_origin && (
                <span className="platform-country muted">{p.country_of_origin}</span>
              )}
            </li>
          ))}
          {!loading && platforms.length === 0 && (
            <li className="muted">No platforms match the current filters.</li>
          )}
        </ul>

        {selectedPlatform && (
          <aside className="platform-detail">
            <div className="detail-header">
              <h3>{selectedPlatform.platform_name}</h3>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelectedPlatform(null)}>
                <X size={14} />
              </button>
            </div>
            <div className="row"><span className="label">Family</span><span>{selectedPlatform.platform_family}</span></div>
            {selectedPlatform.country_of_origin && (
              <div className="row"><span className="label">Country</span><span>{selectedPlatform.country_of_origin}</span></div>
            )}
            {selectedPlatform.role && (
              <div className="row"><span className="label">Role</span><span>{selectedPlatform.role}</span></div>
            )}
            <div className="row"><span className="label">View domains</span><span>{selectedPlatform.view_domains.join(", ") || "—"}</span></div>
            <div className="chip-gallery">
              {selectedPlatform.chips.map((c) => (
                <figure key={c.id} className="chip-card">
                  <img
                    src={`/api/reference-chips/${c.id}/image`}
                    alt={`chip from ${c.source_dataset}`}
                    loading="lazy"
                  />
                  <figcaption className="mono">
                    {c.source_dataset} · {c.license_spdx}
                    {c.attribution ? <> · {c.attribution}</> : null}
                  </figcaption>
                </figure>
              ))}
              {selectedPlatform.chips.length === 0 && (
                <span className="muted">No chips yet for this platform.</span>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
```

Adjust the `ViewHeader` import path and prop shape to match what `AlertsView` actually uses. If `ViewHeader` doesn't accept `actions`, render filters inline instead.

- [ ] **Step 3: Add CSS for the platforms grid + chip gallery**

Same approach as Task 3 — add minimal rules that blend with the existing admin views. The grid is two columns (list left, detail right). Chip gallery is a flex-wrap of small thumbnails.

- [ ] **Step 4: Build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/admin/ReferencePlatformsView.tsx
# plus the CSS file you modified
git commit -m "feat(reference-db): ReferencePlatformsView admin tab"
```

---

## Task 6 — Register the admin tab in `AdminScreen.tsx`

**Files:**
- Modify: `frontend/src/components/AdminScreen.tsx`

- [ ] **Step 1: Add the tab to the `NAV` array**

Open `/nvme/osint/frontend/src/components/AdminScreen.tsx`. Find the `NAV` constant (lines ~64-79 per the exploration). Add a new entry:

```tsx
{ key: 'reference-platforms', label: 'Reference platforms', Icon: Database },
```

You'll also need to add `'reference-platforms'` to the `AdminTab` type union (look for `type AdminTab = ... ;` near the top of the file).

Add the import: `import { Database } from "lucide-react";` (if not already imported).

- [ ] **Step 2: Add the conditional render**

Below the NAV array, the file has a conditional rendering block (something like `{activeTab === 'ontology' && <OntologyView />}` per Plan E's exploration). Add:

```tsx
{activeTab === 'reference-platforms' && <ReferencePlatformsView onCount={(n) => updateCount('reference-platforms', n)} />}
```

(Adjust to match the actual existing function — the count-update mechanism may differ. If `onCount` isn't used by other tabs, just drop the prop.)

Add the import: `import ReferencePlatformsView from "./admin/ReferencePlatformsView";`.

- [ ] **Step 3: Build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build. The TS type union must include `'reference-platforms'` or the conditional render will error.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AdminScreen.tsx
git commit -m "feat(reference-db): register Reference Platforms admin tab in AdminScreen NAV"
```

---

## Task 7 — Add Product Tour steps

**Files:**
- Modify: `frontend/src/components/tour/tourSteps.ts`

CLAUDE.md hard rule 9 requires the tour to track any new interactive control. Add two steps: one for the IdentificationPanel in the Map workspace, one for the admin Reference Platforms tab.

- [ ] **Step 1: Add the steps**

Open `/nvme/osint/frontend/src/components/tour/tourSteps.ts`. Find the section covering the SelectionPanel (around the "tab-details" step the exploration sampled — line ~298). Insert a new step after the Candidate Links step:

```tsx
{
  id: "identification-panel",
  selector: "[data-tour=\"identification-panel\"]",
  title: "Platform identification",
  body: "Top reference-DB platform candidates for this detection. Auto-applied if score ≥ 0.85 (configurable). Use Approve to lock the analyst-asserted identity, Reject to discard, or Re-identify to re-run the lookup.",
  placement: "left",
},
```

For the admin tour (if the existing tour visits Admin tabs), add a step in the appropriate location:

```tsx
{
  id: "admin-reference-platforms",
  selector: "[data-tour=\"admin-reference-platforms\"]",
  title: "Reference platforms",
  body: "Browse the curated reference DB (DOTA, xView, RarePlanes, …). Each platform shows its source chips with license + attribution. Used by the auto-identify path to score new detections.",
  placement: "left",
},
```

If the existing tour doesn't cover Admin tabs, only add the first step. Verify by reading the file's existing step list.

- [ ] **Step 2: Confirm the data-tour attributes are in place**

```bash
grep -r "data-tour=" /nvme/osint/frontend/src/components/ | grep -E "identification-panel|admin-reference-platforms"
```

Expected: at least two matches (one in `IdentificationPanel.tsx`, one in `ReferencePlatformsView.tsx`). If either is missing, go back and add it before committing the tour change.

- [ ] **Step 3: Build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/tour/tourSteps.ts
git commit -m "feat(reference-db): tour steps for IdentificationPanel + admin Reference Platforms"
```

---

## Task 8 — Documentation

**Files:**
- Create: `docs/frontend/identification-panel.md`
- Create: `docs/frontend/admin-reference-platforms.md`
- Modify: `docs/frontend/map-selection-panel.md` (note new Identification subsection)
- Modify: `docs/backend-routers/reference-platforms-router.md` (add chip-serving route to table)
- Modify: `docs/INDEX.txt`

- [ ] **Step 1: Write the IdentificationPanel module doc**

Create `/nvme/osint/docs/frontend/identification-panel.md` following the six-section template. Reference the related decisions and backend routes.

- [ ] **Step 2: Write the admin view doc**

Create `/nvme/osint/docs/frontend/admin-reference-platforms.md` — same template.

- [ ] **Step 3: Update the SelectionPanel doc**

Open `/nvme/osint/docs/frontend/map-selection-panel.md` and add a sentence to the Details-tab section: "Includes an Identification subsection ([identification-panel.md](identification-panel.md)) between Taxonomy and Allegiance with top-k reference-DB candidates and approve/reject controls."

- [ ] **Step 4: Update the router doc**

Open `/nvme/osint/docs/backend-routers/reference-platforms-router.md` and append a row to the route table:

```
| GET | `/api/reference-chips/{chip_id}/image` | Stream the chip image at `reference_chips.chip_path` with path-traversal guard. 403 if path is not under `/data/datasets/`. |
```

Also bump the "Lines: ~400" header to the actual current line count (probably ~440 after the new route).

- [ ] **Step 5: Update INDEX.txt**

Add two new entries (canonical tags only):

```
frontend/identification-panel.md|frontend|top-k reference-DB candidates with approve/reject in SelectionPanel Details tab
frontend/admin-reference-platforms.md|frontend|admin tab to browse curated reference platforms with chip thumbnails
```

Place each in correct within-section alphabetical position.

- [ ] **Step 6: Commit**

```bash
git add docs/frontend/identification-panel.md docs/frontend/admin-reference-platforms.md docs/frontend/map-selection-panel.md docs/backend-routers/reference-platforms-router.md docs/INDEX.txt
git commit -m "docs(reference-db): frontend module docs + router chip-serving entry + INDEX"
```

---

## Task 9 — Final verification

**Files:** none modified.

- [ ] **Step 1: Backend test suite**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py tests/test_reference_platform_baker.py tests/test_reference_platform_auto_identify.py tests/test_reference_platforms_router.py tests/test_reference_chip_image_route.py tests/test_pgvector_pool_registration.py tests/test_object_details.py 2>&1 | tail -3"
```

Expected: 44 passed (40 prior + 4 new chip-image tests).

- [ ] **Step 2: Frontend build**

```bash
cd /nvme/osint/frontend && npm run build 2>&1 | tail -10
```

Expected: clean build, no TS errors. Note the output bundle size — if it grew by > 50 KB, something unexpected was pulled in.

- [ ] **Step 3: Live curl exercise of the chip route**

```bash
docker compose exec -T backend bash -lc '
USER=$(grep ^ADMIN_USERNAME /app/.env | cut -d= -f2)
PASS=$(grep ^ADMIN_PASSWORD /app/.env | cut -d= -f2)
curl -s -c /tmp/s.txt -X POST -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  http://localhost:8080/api/auth/login > /dev/null
# Get any DOTA chip
CID=$(curl -s -b /tmp/s.txt "http://localhost:8080/api/reference-platforms?limit=20" \
  | python -c "import sys,json; d=json.load(sys.stdin); print(d[\"platforms\"][0][\"id\"])")
DETAIL=$(curl -s -b /tmp/s.txt "http://localhost:8080/api/reference-platforms/$CID")
CHIP=$(echo "$DETAIL" | python -c "import sys,json; d=json.load(sys.stdin); print(d[\"chips\"][0][\"id\"] if d[\"chips\"] else \"\")")
if [ -n "$CHIP" ]; then
  curl -s -b /tmp/s.txt -o /tmp/chip.png -w "chip_http=%{http_code} size=%{size_download}\n" \
    "http://localhost:8080/api/reference-chips/$CHIP/image"
  file /tmp/chip.png 2>&1 | head -1
else
  echo "no chip found for platform $CID"
fi
'
```

Expected: `chip_http=200 size=>0`, `file` shows "PNG image data" or similar.

- [ ] **Step 4: Optional — Playwright smoke**

If Playwright is configured:

```bash
cd /nvme/osint/frontend && npx playwright test 2>&1 | tail -10
```

If pre-existing tests fail for unrelated reasons (e.g. baseline screenshots stale), note but don't block.

- [ ] **Step 5: Scope check**

```bash
# Plan E's first commit is Task 1's chip-route commit. Find it:
git log --format='%h %s' 98a825fde9be6fe14aaf27855eea5a8651c27bc3..HEAD | head -20
git diff --name-only 98a825fde9be6fe14aaf27855eea5a8651c27bc3..HEAD | sort
```

Expected files (~17 in Plan E scope):
- `backend/routers/reference_platforms.py` (Task 1 — chip route)
- `backend/tests/test_reference_chip_image_route.py` (Task 1)
- `frontend/src/components/ObjectDetailsForm.tsx` (Task 2)
- `frontend/src/components/map/IdentificationPanel.tsx` (Task 3, new)
- `frontend/src/components/map/SelectionPanel.tsx` (Task 4)
- `frontend/src/components/admin/ReferencePlatformsView.tsx` (Task 5, new)
- `frontend/src/components/AdminScreen.tsx` (Task 6)
- `frontend/src/components/tour/tourSteps.ts` (Task 7)
- CSS file(s) for Task 3/5
- `docs/frontend/identification-panel.md` (Task 8, new)
- `docs/frontend/admin-reference-platforms.md` (Task 8, new)
- `docs/frontend/map-selection-panel.md` (Task 8)
- `docs/backend-routers/reference-platforms-router.md` (Task 8)
- `docs/INDEX.txt` (Task 8)
- `docs/superpowers/plans/2026-05-27-reference-db-plan-e-frontend.md` (this file)

NOTHING in `inference-sam3/`, `backend/worker_legacy.py`, or `backend/schemas.py` (Plan E doesn't change schemas).

## Definition of Done

- `GET /api/reference-chips/{id}/image` returns the chip PNG with path-traversal guard; 4 new backend tests pass; full reference-DB suite is 44 green.
- `IdentificationPanel.tsx` renders top-k candidates with chip thumbnails and approve/reject buttons; mounted in SelectionPanel Details tab; live curl + manual map-workspace check confirms it works against the live backend.
- `ObjectDetailsForm.tsx` shows the four `platform_*` fields read-only when populated.
- `ReferencePlatformsView.tsx` admin tab lists platforms, filters by family/country, shows a detail with chips.
- `AdminScreen.tsx` NAV array includes `reference-platforms`.
- Two new tour steps with matching `data-tour` attributes on the components.
- Three new docs (panel + admin view + router chip-serving entry) + INDEX update.
- No `inference-sam3/`, no `backend/worker_legacy.py`, no `backend/schemas.py` modified in this plan.

## What this plan does NOT do

- WebSocket live updates for identification candidates — deferred. The `useEventStream` hook is wired and ready; emitting from approve/reject is a Plan-F or hygiene task.
- Bulk approve / reject UI — single-candidate buttons only.
- A way to override the auto-applied platform without going through approve — analysts use approve on a different candidate, or reject + re-identify, to override. (A "manual override" form would be Plan-F.)
- Refresh-all-identifications maintenance UI — Plan-F.
- I18n / accessibility audit — Plan-F.

Hand back to the user when "Definition of Done" is fully checked.
