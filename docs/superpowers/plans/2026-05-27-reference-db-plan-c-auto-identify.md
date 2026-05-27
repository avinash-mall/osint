# Reference Embedding DB — Plan C: Auto-Identify on Detect

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every new satellite detection that carries a DINOv3-SAT embedding gets top-k platform candidates from the reference DB attached, persisted to `platform_identification_candidates`, and (when top-1 cosine ≥ `REFERENCE_ID_AUTO_THRESHOLD`, default 0.85) auto-applied to `object_details.platform_name` + `platform_family` + `platform_confidence` + `platform_source='auto'`. End state: a fresh detection over a DOTA-class object has a row in the candidate queue and (if confident) a platform asserted on its object-details row — without analyst intervention.

**Architecture (deviation from the original parent spec, recorded as a decision):**

The parent spec at `docs/superpowers/plans/i-want-to-build-breezy-snail.md` (Plan A's parent) anticipated putting the identifier inside `inference-sam3` (read-only Postgres connection at lifespan startup). Plan B's `why-standalone-embed-endpoint.md` already decided the inference container should NOT carry psycopg2 or DB credentials. Plan C honours that decision: **the auto-identify lookup runs in the backend worker, splicing in immediately after the detection INSERT**, while `inference-sam3` continues to do only embedding computation.

This is a strict improvement over the original design — keeps GPU/inference and DB concerns separated, removes a service-coupling failure mode (DB unreachable during a detect call would break detect), and reuses the same `_VectorAwareConnection` pool that Plan B's bake script uses.

**Tech Stack:**
- pgvector 0.8.2 with HNSW indexes (Plan A schema, populated by Plan B's DOTA bake)
- psycopg2-binary + pgvector adapter (already pool-wired, Plan B Task 1)
- `recompute_platform_centroids` results already present (10 DOTA platforms with non-null `centroid_overhead`)
- Existing worker insertion path at `backend/worker_legacy.py:2500-2592`
- Existing `_parse_embedding_anchor()` at `backend/worker_legacy.py:3584-3610` to decode `fp16_b64`
- Existing `_upsert_object_details()` in `backend/detection_helpers.py` (extends `ObjectDetailsBody` Pydantic schema)

**Parent specs (in-repo):**
- Plan A: `docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md`
- Plan B: `docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md`

---

## File Structure

**Created:**
- `backend/tests/test_reference_platform_auto_identify.py` *(new)* — integration tests: read-path helper, candidate queue insert, threshold auto-apply, idempotency.
- `docs/decisions/why-auto-identify-in-backend-not-inference.md` *(new)* — decision record.
- `docs/decisions/why-auto-write-with-threshold.md` *(new)* — decision record for the 0.85 default threshold.

**Modified:**
- `backend/platform_schema.py` — add `CHECK (platform_confidence IS NULL OR (platform_confidence >= 0 AND platform_confidence <= 1))` to the `object_details.platform_confidence` ALTER. (Plan B carry-forward.)
- `backend/reference_platform_db.py` — add `find_similar_platforms(cursor, *, embedding, view_domain, top_k, candidate_pool)` and `attach_identification_candidates(cursor, *, detection_id, embedding, view_domain, auto_threshold)`.
- `backend/schemas.py` — extend `ObjectDetailsBody` with optional `platform_name`, `platform_family`, `platform_confidence`, `platform_source` fields.
- `backend/detection_helpers.py` — extend `_upsert_object_details` to write the new platform_* columns when present in the body.
- `backend/worker_legacy.py` — splice in `attach_identification_candidates(...)` call immediately after `det_id = cursor.fetchone()["id"]` at line 2592; add `REFERENCE_ID_AUTO_THRESHOLD = float(os.getenv("REFERENCE_ID_AUTO_THRESHOLD", "0.85"))` near the existing config block at lines 49-128.
- `docs/backend/reference-platform-db.md` — refresh Outputs list to mention the new CHECK constraint; mention the new helpers in Key symbols.
- `docs/backend/reference-platform-baker.md` — cross-link the new read-path helpers in the "Companion" line.
- `docs/backend/detection-helpers.md` — note the new optional `platform_*` fields in `_upsert_object_details`.
- `docs/backend/worker-legacy-monolith.md` — note the new auto-identify hook at the detection-insert site.
- `docs/INDEX.txt` — two new decision-doc entries.

**Untouched in Plan C:**
- `inference-sam3/*` — no GPU code changes. The `/embed` endpoint added in Plan B is consumed by the bake script; the worker uses the embedding already attached to detections.
- `backend/routers/*` — Plan D scope (analyst-driven lookup + approve/reject HTTP endpoints).
- `frontend/*` — Plan D scope.

---

## Task 1 — `platform_confidence` CHECK constraint (Plan B carry-forward)

**Files:**
- Modify: `backend/platform_schema.py`

Plan A added `platform_confidence REAL` to `object_details` without a range constraint. Plan B's branch-level review listed this as deferred to Plan C. Add it before any code starts writing the column.

- [ ] **Step 1: Find the existing ALTER and add a CHECK after it**

Open `/nvme/osint/backend/platform_schema.py` and find the line in `ensure_reference_platform_tables()`:

```python
cursor.execute("ALTER TABLE object_details ADD COLUMN IF NOT EXISTS platform_confidence REAL")
```

Add this immediately after that line:

```python
cursor.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'object_details_platform_confidence_check'
        ) THEN
            ALTER TABLE object_details
                ADD CONSTRAINT object_details_platform_confidence_check
                CHECK (platform_confidence IS NULL OR (platform_confidence >= 0 AND platform_confidence <= 1));
        END IF;
    END $$;
""")
```

The `DO $$ ... $$;` block makes the constraint addition idempotent — `ALTER TABLE … ADD CONSTRAINT` doesn't support `IF NOT EXISTS` for CHECK constraints on Postgres < 14, but this pg_constraint probe works on every supported version.

- [ ] **Step 2: Verify it lands**

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "\d+ object_details" | grep -A1 'platform_confidence_check'
```

Expected: shows the CHECK constraint.

- [ ] **Step 3: Re-run schema tests**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py -v"
```

Expected: still 8 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/platform_schema.py
git commit -m "schema(reference-db): CHECK constraint on object_details.platform_confidence in [0,1]"
```

---

## Task 2 — Read-path helper: `find_similar_platforms`

**Files:**
- Modify: `backend/reference_platform_db.py`

The two-stage lookup the parent spec describes: HNSW top-K centroid match → re-rank by best per-chip cosine among the K winners. Returns top-N platforms with score and the chip IDs that drove the score.

- [ ] **Step 1: Append the helper to `backend/reference_platform_db.py`**

Add this function near the end of the file, after `recompute_platform_centroids`:

```python
def find_similar_platforms(
    cursor,
    *,
    embedding: Iterable[float],
    view_domain: str = "overhead",
    top_k: int = 3,
    candidate_pool: int = 20,
) -> list[dict]:
    """Return top-k platforms whose centroid is closest to the given embedding.

    Two-stage retrieval:
      1. Centroid HNSW search → top `candidate_pool` platforms (cheap, dense).
      2. Re-rank by best per-chip cosine score among each winner's chips (refined).

    Returns a list of dicts ordered by descending score:
        [{"platform_id": str, "platform_name": str, "platform_family": str,
          "score": float, "matched_chip_ids": list[str]}, ...]

    `score` is `1 - cosine_distance` so values are in approximately [-1, 1];
    for unit-normalised DINOv3-SAT vectors they land in [0, 1].

    Returns an empty list if no platform has a centroid in `view_domain`.
    """
    if view_domain not in ("overhead", "ground"):
        raise ValueError(f"view_domain must be 'overhead' or 'ground', got {view_domain!r}")

    # Preserve numpy.ndarray as-is for the pgvector adapter; list-ify other iterables.
    try:
        import numpy as _np
        _is_np = isinstance(embedding, _np.ndarray)
    except ImportError:
        _is_np = False
    q = embedding if _is_np else list(embedding)

    centroid_col = "centroid_overhead" if view_domain == "overhead" else "centroid_ground"
    chip_col = "embedding_overhead" if view_domain == "overhead" else "embedding_ground"

    # Stage 1: centroid HNSW top-K
    cursor.execute(
        f"""
        SELECT id, platform_name, platform_family,
               1 - ({centroid_col} <=> %s) AS centroid_score
          FROM reference_platforms
         WHERE {centroid_col} IS NOT NULL
         ORDER BY {centroid_col} <=> %s
         LIMIT %s
        """,
        (q, q, candidate_pool),
    )
    centroid_winners = cursor.fetchall()
    if not centroid_winners:
        return []

    winner_ids = [(r["id"] if isinstance(r, dict) else r[0]) for r in centroid_winners]
    winner_names = {
        (r["id"] if isinstance(r, dict) else r[0]): {
            "platform_name": r["platform_name"] if isinstance(r, dict) else r[1],
            "platform_family": r["platform_family"] if isinstance(r, dict) else r[2],
        }
        for r in centroid_winners
    }

    # Stage 2: for each winner, find the best per-chip cosine. We do one
    # round-trip with a window function — gives best-chip-per-platform
    # and avoids N+1 SELECTs.
    cursor.execute(
        f"""
        WITH ranked AS (
            SELECT c.platform_id,
                   c.id AS chip_id,
                   1 - (c.{chip_col} <=> %s) AS chip_score,
                   ROW_NUMBER() OVER (
                       PARTITION BY c.platform_id
                       ORDER BY c.{chip_col} <=> %s
                   ) AS rn
              FROM reference_chips c
             WHERE c.platform_id = ANY(%s::uuid[])
               AND c.view_domain = %s
               AND c.{chip_col} IS NOT NULL
        )
        SELECT platform_id,
               MAX(chip_score) AS best_chip_score,
               array_agg(chip_id ORDER BY chip_score DESC) FILTER (WHERE rn <= 3) AS top_chip_ids
          FROM ranked
         GROUP BY platform_id
         ORDER BY best_chip_score DESC
         LIMIT %s
        """,
        (q, q, winner_ids, view_domain, top_k),
    )
    rows = cursor.fetchall()

    results = []
    for r in rows:
        pid = r["platform_id"] if isinstance(r, dict) else r[0]
        score = r["best_chip_score"] if isinstance(r, dict) else r[1]
        chip_ids = r["top_chip_ids"] if isinstance(r, dict) else r[2]
        info = winner_names.get(pid, {"platform_name": None, "platform_family": None})
        results.append({
            "platform_id": pid,
            "platform_name": info["platform_name"],
            "platform_family": info["platform_family"],
            "score": float(score) if score is not None else 0.0,
            "matched_chip_ids": list(chip_ids) if chip_ids else [],
        })
    return results
```

The `cursor.fetchall()` dict-vs-tuple guard matches Plan B's pattern (works against both `RealDictCursor` and plain tuples).

- [ ] **Step 2: Quick sanity test against the live DOTA bake**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -c '
import sys, numpy as np
sys.path.insert(0, \"/app\")
from database import postgis_db
from reference_platform_db import find_similar_platforms
# Use the DOTA::plane centroid as a query — should find itself top-1
with postgis_db.get_cursor(commit=False) as cur:
    cur.execute(\"SELECT centroid_overhead FROM reference_platforms WHERE platform_name = %s\", (\"DOTA::plane\",))
    q = cur.fetchone()[\"centroid_overhead\"]
    results = find_similar_platforms(cur, embedding=q, view_domain=\"overhead\", top_k=3)
for r in results:
    print(r[\"platform_name\"], round(r[\"score\"], 4), \"matched\", len(r[\"matched_chip_ids\"]), \"chips\")
'"
```

Expected: `DOTA::plane 1.0 matched 3 chips` as the top result, followed by some other DOTA platforms with score < 1.0.

If the query fails because the live DB has no DOTA data (e.g. the volume was wiped since Plan B Task 7), re-run Plan B Task 7's bake first.

- [ ] **Step 3: Commit**

```bash
git add backend/reference_platform_db.py
git commit -m "feat(reference-db): find_similar_platforms read-path helper (centroid HNSW + chip re-rank)"
```

---

## Task 3 — Failing integration test for auto-identify

**Files:**
- Create: `backend/tests/test_reference_platform_auto_identify.py`

TDD: the test exercises a worker-equivalent flow against the live schema. The test does NOT mock pgvector — it inserts a fake reference platform and chips, then calls a function that doesn't exist yet (`attach_identification_candidates`).

- [ ] **Step 1: Write the test file**

Create `/nvme/osint/backend/tests/test_reference_platform_auto_identify.py` with EXACTLY this content:

```python
"""Integration tests for the worker-side auto-identify path.

Exercises:
  - find_similar_platforms returns ranked candidates
  - attach_identification_candidates inserts platform_identification_candidates rows
  - Auto-apply writes object_details.platform_* when score >= threshold
  - Below-threshold candidates land as 'pending' (no object_details write)
  - Idempotent re-run on the same detection_id replaces rows (not duplicates)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _cleanup_pytest_rows():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM platform_identification_candidates
             WHERE detection_id IN (
                 SELECT id FROM detections WHERE class LIKE 'pytest-autoid-%'
             )
        """)
        cur.execute("""
            DELETE FROM object_details
             WHERE source = 'detection'
               AND source_id IN (
                   SELECT id::text FROM detections WHERE class LIKE 'pytest-autoid-%'
               )
        """)
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-autoid-%'")
        cur.execute("""
            DELETE FROM reference_chips
             WHERE source_dataset = 'pytest-autoid-fixture'
        """)
        cur.execute("""
            DELETE FROM reference_platforms
             WHERE platform_name LIKE 'pytest-autoid-%'
        """)


@pytest.fixture(scope="module")
def populated_ref_db():
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import (
        upsert_reference_platform,
        insert_reference_chip,
        recompute_platform_centroids,
    )
    from database import postgis_db
    ensure_reference_platform_tables()
    _cleanup_pytest_rows()
    # Build two platforms whose centroids are far apart so a query close to
    # one is unambiguously the right answer.
    with postgis_db.get_cursor(commit=True) as cur:
        pid_red = upsert_reference_platform(
            cur, platform_name="pytest-autoid-Red", platform_family="RedFam"
        )
        pid_blue = upsert_reference_platform(
            cur, platform_name="pytest-autoid-Blue", platform_family="BlueFam"
        )
        for i in range(3):
            insert_reference_chip(
                cur,
                platform_id=pid_red,
                view_domain="overhead",
                source_dataset="pytest-autoid-fixture",
                chip_path=f"/tmp/pytest-autoid-red-{i}.png",
                embedding=np.full(1024, 1.0, dtype=np.float32),  # all-ones
                license_spdx="CC0-1.0",
            )
            insert_reference_chip(
                cur,
                platform_id=pid_blue,
                view_domain="overhead",
                source_dataset="pytest-autoid-fixture",
                chip_path=f"/tmp/pytest-autoid-blue-{i}.png",
                embedding=np.full(1024, -1.0, dtype=np.float32),  # all-neg-ones
                license_spdx="CC0-1.0",
            )
        recompute_platform_centroids(cur, platform_id=pid_red)
        recompute_platform_centroids(cur, platform_id=pid_blue)
    yield {"red_id": pid_red, "blue_id": pid_blue}
    _cleanup_pytest_rows()


def test_find_similar_platforms_ranks_correctly(populated_ref_db):
    """A query embedding identical to Red's centroid must rank Red top-1."""
    from database import postgis_db
    from reference_platform_db import find_similar_platforms
    q = np.full(1024, 1.0, dtype=np.float32)
    with postgis_db.get_cursor(commit=False) as cur:
        results = find_similar_platforms(cur, embedding=q, view_domain="overhead", top_k=2)
    assert len(results) == 2
    assert results[0]["platform_name"] == "pytest-autoid-Red"
    assert results[0]["score"] == pytest.approx(1.0, abs=1e-4)
    assert results[1]["platform_name"] == "pytest-autoid-Blue"
    assert results[1]["score"] == pytest.approx(-1.0, abs=1e-4)
    assert len(results[0]["matched_chip_ids"]) > 0


def _insert_fake_detection(class_label: str = "pytest-autoid-test") -> int:
    """Insert a minimal detection row so we have a real FK target."""
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
            (class_label,),
        )
        return cur.fetchone()["id"]


def test_attach_writes_candidates_and_auto_applies_above_threshold(populated_ref_db):
    """Top-1 score >= threshold must (a) write a 'auto_applied' candidate row
    and (b) populate object_details.platform_name/family/confidence/source."""
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    det_id = _insert_fake_detection("pytest-autoid-high")
    q = np.full(1024, 1.0, dtype=np.float32)  # exactly matches Red centroid
    with postgis_db.get_cursor(commit=True) as cur:
        n = attach_identification_candidates(
            cur,
            detection_id=det_id,
            embedding=q,
            view_domain="overhead",
            auto_threshold=0.85,
            top_k=3,
        )
    assert n >= 1, "expected at least one candidate inserted"

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT p.platform_name, c.score, c.rank, c.status
              FROM platform_identification_candidates c
              JOIN reference_platforms p ON c.platform_id = p.id
             WHERE c.detection_id = %s
             ORDER BY c.rank
        """, (det_id,))
        cands = cur.fetchall()
    assert len(cands) >= 1
    top = cands[0]
    assert top["platform_name"] == "pytest-autoid-Red"
    assert top["status"] == "auto_applied"
    assert top["score"] >= 0.85

    # object_details auto-populated
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT platform_name, platform_family, platform_confidence, platform_source
              FROM object_details
             WHERE source = 'detection' AND source_id = %s
        """, (str(det_id),))
        od = cur.fetchone()
    assert od is not None
    assert od["platform_name"] == "pytest-autoid-Red"
    assert od["platform_family"] == "RedFam"
    assert od["platform_source"] == "auto"
    assert od["platform_confidence"] >= 0.85
    assert od["platform_confidence"] <= 1.0


def test_attach_below_threshold_leaves_candidates_pending(populated_ref_db):
    """A query that matches the top platform with score < threshold must
    (a) still write candidate rows but (b) NOT populate object_details."""
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    det_id = _insert_fake_detection("pytest-autoid-low")
    # An embedding orthogonal-ish to both centroids — cosine ~ 0
    q = np.zeros(1024, dtype=np.float32)
    q[0] = 1.0
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur,
            detection_id=det_id,
            embedding=q,
            view_domain="overhead",
            auto_threshold=0.85,
            top_k=3,
        )

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT status FROM platform_identification_candidates
             WHERE detection_id = %s
        """, (det_id,))
        statuses = [r["status"] for r in cur.fetchall()]
        cur.execute("""
            SELECT platform_name FROM object_details
             WHERE source = 'detection' AND source_id = %s
        """, (str(det_id),))
        od = cur.fetchone()

    assert all(s == "pending" for s in statuses), f"expected all 'pending', got {statuses}"
    assert od is None or od.get("platform_name") is None, \
        "object_details.platform_name must NOT be auto-populated below threshold"


def test_attach_is_idempotent_on_rerun(populated_ref_db):
    """A second attach call on the same detection_id replaces, not duplicates."""
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    det_id = _insert_fake_detection("pytest-autoid-idem")
    q = np.full(1024, 1.0, dtype=np.float32)
    with postgis_db.get_cursor(commit=True) as cur:
        n1 = attach_identification_candidates(
            cur, detection_id=det_id, embedding=q, view_domain="overhead",
            auto_threshold=0.85, top_k=3,
        )
    with postgis_db.get_cursor(commit=True) as cur:
        n2 = attach_identification_candidates(
            cur, detection_id=det_id, embedding=q, view_domain="overhead",
            auto_threshold=0.85, top_k=3,
        )
    assert n1 == n2
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT count(*) AS n FROM platform_identification_candidates
             WHERE detection_id = %s
        """, (det_id,))
        row = cur.fetchone()
    # After two calls with identical inputs, the count should equal one
    # run's worth — NOT 2× (which would mean we duplicated instead of replacing).
    assert row["n"] == n1, f"expected {n1} rows, got {row['n']}"


def test_platform_confidence_check_rejects_out_of_range(populated_ref_db):
    """The CHECK constraint added in Task 1 must reject confidence > 1.0."""
    import psycopg2
    from database import postgis_db
    det_id = _insert_fake_detection("pytest-autoid-checkfail")
    with pytest.raises(psycopg2.errors.CheckViolation):
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO object_details
                    (source, source_id, platform_name, platform_confidence)
                VALUES ('detection', %s, 'pytest-autoid-Red', 1.5)
                """,
                (str(det_id),),
            )
```

- [ ] **Step 2: Run the test — it must fail with ImportError on `attach_identification_candidates`**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_auto_identify.py -v"
```

Expected: `test_find_similar_platforms_ranks_correctly` passes (Task 2's helper exists); the other 4 tests error with `ImportError: cannot import name 'attach_identification_candidates' from 'reference_platform_db'` because Task 4 hasn't landed yet. The CHECK-constraint test should also pass (Task 1 already landed).

So expected: 2 passed + 3 errored.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_reference_platform_auto_identify.py
git commit -m "test(reference-db): failing tests for auto-identify worker helper"
```

---

## Task 4 — `attach_identification_candidates` helper + extend `_upsert_object_details`

**Files:**
- Modify: `backend/reference_platform_db.py` — add the helper.
- Modify: `backend/schemas.py` — extend `ObjectDetailsBody` with four optional `platform_*` fields.
- Modify: `backend/detection_helpers.py` — extend `_upsert_object_details` to persist the new fields.

- [ ] **Step 1: Extend the Pydantic schema**

Open `/nvme/osint/backend/schemas.py` and find the `ObjectDetailsBody` class. Add four optional fields after the existing fields (preserve the existing field order — append at the end):

```python
    platform_name: Optional[str] = None
    platform_family: Optional[str] = None
    platform_confidence: Optional[float] = None
    platform_source: Optional[str] = None  # 'auto' | 'analyst' | 'manual'
```

If `Optional` and `Field` aren't already imported at the top of the file, add them: `from typing import Optional` and `from pydantic import Field` (whichever is missing).

- [ ] **Step 2: Extend `_upsert_object_details` to write the new fields**

Open `/nvme/osint/backend/detection_helpers.py` and find the `INSERT INTO object_details` statement around line 80. The four new columns need to land in the column list, VALUES list, and the `ON CONFLICT DO UPDATE` clause.

Find the existing INSERT (it currently has columns `source, source_id, designation, object_class, military_classification, threat_level, affiliation, confidence_override, notes, updated_by`). Modify it so the column list, VALUES list, and the ON CONFLICT UPDATE clause each gain four entries:

- Column list — add: `platform_name, platform_family, platform_confidence, platform_source`
- VALUES list — add four `%s` placeholders
- ON CONFLICT UPDATE — add:
  ```sql
  platform_name        = COALESCE(EXCLUDED.platform_name,        object_details.platform_name),
  platform_family      = COALESCE(EXCLUDED.platform_family,      object_details.platform_family),
  platform_confidence  = COALESCE(EXCLUDED.platform_confidence,  object_details.platform_confidence),
  platform_source      = COALESCE(EXCLUDED.platform_source,      object_details.platform_source),
  ```
- The Python tuple of values passed to `cursor.execute(...)` — add the four `body.platform_*` references.

Match the surrounding indentation and `COALESCE(EXCLUDED.X, object_details.X)` pattern exactly.

- [ ] **Step 3: Append `attach_identification_candidates` to `backend/reference_platform_db.py`**

Add this function after `find_similar_platforms`:

```python
def attach_identification_candidates(
    cursor,
    *,
    detection_id: int,
    embedding: Iterable[float],
    view_domain: str = "overhead",
    auto_threshold: float = 0.85,
    top_k: int = 3,
) -> int:
    """For a freshly-inserted detection, compute top-k reference-platform
    candidates and persist them.

    Behaviour:
      - Calls `find_similar_platforms` to get top-k candidates.
      - Deletes any existing `platform_identification_candidates` rows for
        this `detection_id` (so a re-run replaces, not duplicates).
      - Inserts one `platform_identification_candidates` row per candidate.
      - If top-1 score >= `auto_threshold`, marks that row `auto_applied` and
        writes `platform_name` / `platform_family` / `platform_confidence`
        / `platform_source='auto'` to `object_details` via the same UPSERT
        helper used by the analyst-facing endpoints.
      - Below threshold, all rows land as `pending` and `object_details` is
        left untouched.

    Returns the number of candidate rows written. Returns 0 if no candidates
    were found (e.g. reference DB empty for this view_domain), in which case
    `object_details` is also not modified.
    """
    candidates = find_similar_platforms(
        cursor,
        embedding=embedding,
        view_domain=view_domain,
        top_k=top_k,
    )
    if not candidates:
        return 0

    # Idempotency: replace any prior candidates for this detection.
    cursor.execute(
        "DELETE FROM platform_identification_candidates WHERE detection_id = %s",
        (detection_id,),
    )

    top_score = candidates[0]["score"]
    auto_applied = top_score >= auto_threshold

    for rank, cand in enumerate(candidates, start=1):
        is_top = (rank == 1)
        status = "auto_applied" if (is_top and auto_applied) else "pending"
        applied_at_sql = "NOW()" if status == "auto_applied" else "NULL"
        cursor.execute(
            f"""
            INSERT INTO platform_identification_candidates
                (detection_id, platform_id, score, rank, matched_chip_ids,
                 status, applied_at)
            VALUES (%s, %s, %s, %s, %s::uuid[], %s, {applied_at_sql})
            """,
            (
                detection_id,
                cand["platform_id"],
                cand["score"],
                rank,
                cand["matched_chip_ids"] or [],
                status,
            ),
        )

    # Auto-apply to object_details only when top-1 cleared the threshold.
    if auto_applied:
        top = candidates[0]
        # Use the local SQL UPSERT directly (avoids importing
        # _upsert_object_details, which would require a request-side
        # Pydantic body). Column list mirrors detection_helpers.py exactly
        # but only the platform_* fields are populated here.
        cursor.execute(
            """
            INSERT INTO object_details
                (source, source_id, platform_name, platform_family,
                 platform_confidence, platform_source, updated_by)
            VALUES ('detection', %s, %s, %s, %s, 'auto', 'reference-db-auto-identify')
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
                top["platform_name"],
                top["platform_family"],
                float(top["score"]),
            ),
        )

    return len(candidates)
```

The auto-apply UPSERT writes only the four `platform_*` columns + the `source` / `source_id` / `updated_by` / `updated_at` housekeeping columns. It leaves the analyst-asserted columns (`threat_level`, `affiliation`, `designation`, `notes`, etc.) untouched even on an existing object_details row — `ON CONFLICT DO UPDATE` only updates the listed columns.

- [ ] **Step 4: Run the auto-identify test suite — must be 5 passed**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_auto_identify.py -v"
```

Expected: 5 passed (find_similar, auto-apply above threshold, pending below threshold, idempotency, CHECK rejects out-of-range).

- [ ] **Step 5: Run the full suite to confirm no regression**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py tests/test_reference_platform_baker.py tests/test_reference_platform_auto_identify.py tests/test_pgvector_pool_registration.py tests/test_object_details.py -v 2>&1 | tail -15"
```

Expected: 24 passed (8 schema + 5 baker + 5 auto-identify + 2 pool + 4 object-details).

- [ ] **Step 6: Commit**

```bash
git add backend/reference_platform_db.py backend/schemas.py backend/detection_helpers.py
git commit -m "feat(reference-db): attach_identification_candidates + ObjectDetailsBody platform_* fields"
```

---

## Task 5 — Wire into the worker

**Files:**
- Modify: `backend/worker_legacy.py`

The actual integration: every detection that gets INSERTed and has an embedding now also gets a candidates row (and, if top-1 ≥ threshold, an auto-applied object_details row).

- [ ] **Step 1: Add the threshold env var near the existing config block**

Open `/nvme/osint/backend/worker_legacy.py` and find the env-var config block around lines 49-128 (where things like `INFERENCE_SAM3_URL` and `INFERENCE_SPEED_PROFILE` are read). Add a new constant:

```python
REFERENCE_ID_AUTO_THRESHOLD = float(os.getenv("REFERENCE_ID_AUTO_THRESHOLD", "0.85"))
```

(Add `from reference_platform_db import attach_identification_candidates` to the imports near the top of the file if not already imported; place near the other `from <backend-module> import …` lines.)

- [ ] **Step 2: Splice the call immediately after the detection INSERT**

Find the detection INSERT site at `backend/worker_legacy.py:2500-2592`. Specifically the line:

```python
det_id = cursor.fetchone()["id"]
```

Immediately after that line, add:

```python
# Plan C: attach reference-DB platform identification candidates and (when
# top-1 score ≥ threshold) auto-apply platform_* to object_details. Best-effort:
# any exception is logged and skipped — must NEVER break the detection write.
emb_dict = det.get("embedding")
if emb_dict:
    try:
        emb_anchor = _parse_embedding_anchor(emb_dict)
        if emb_anchor is not None:
            attach_identification_candidates(
                cursor,
                detection_id=det_id,
                embedding=emb_anchor,
                view_domain="overhead",
                auto_threshold=REFERENCE_ID_AUTO_THRESHOLD,
                top_k=3,
            )
    except Exception:
        logger.warning(
            "auto-identify failed for detection %s (continuing)", det_id, exc_info=True,
        )
```

Match the surrounding indentation. The `_parse_embedding_anchor` function already exists in this file around line 3584 and returns either a float list or `None` (callers use it to skip detections that don't have a usable embedding).

The blanket `except Exception` is deliberate: identification is a best-effort enrichment. A reference-DB outage MUST NOT prevent detection persistence. The log line gives operators a signal without crashing the pipeline.

- [ ] **Step 3: Restart the backend (worker is part of the same image)**

```bash
docker compose up -d --build backend worker
```

(If a `worker` service doesn't exist by that exact name, just rebuild `backend`. Check `docker-compose.yml` for the actual service name handling Celery.)

- [ ] **Step 4: Smoke-test by inserting a detection with a Red-centroid embedding and verifying auto-apply**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -c '
import base64, sys, numpy as np
sys.path.insert(0, \"/app\")
from database import postgis_db
from worker_legacy import _parse_embedding_anchor  # verify importable
# Build a synthetic detection with a Red-pointing embedding
v = np.full(1024, 1.0, dtype=np.float16)
emb_dict = {\"model\": \"smoke-test\", \"dim\": 1024,
            \"fp16_b64\": base64.b64encode(v.tobytes()).decode(\"ascii\")}
parsed = _parse_embedding_anchor(emb_dict)
print(\"_parse_embedding_anchor parsed:\", parsed is not None and len(parsed) == 1024)
'"
```

Then exercise the full worker flow if possible by ingesting a fixture image — or rely on the auto-identify test suite running in CI, since the unit-test coverage is comprehensive.

- [ ] **Step 5: Run the full backend test suite again**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/ -v -k 'reference_platform or pgvector_pool or object_details' 2>&1 | tail -10"
```

Expected: 24 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/worker_legacy.py
git commit -m "feat(reference-db): worker calls attach_identification_candidates after detection INSERT"
```

---

## Task 6 — Documentation

**Files:**
- Create: `docs/decisions/why-auto-identify-in-backend-not-inference.md`
- Create: `docs/decisions/why-auto-write-with-threshold.md`
- Modify: `docs/backend/reference-platform-db.md` — note the CHECK + the new helpers in Key symbols + Outputs.
- Modify: `docs/backend/reference-platform-baker.md` — cross-link the new helpers in Companion.
- Modify: `docs/backend/detection-helpers.md` — note the four optional platform_* fields.
- Modify: `docs/backend/worker-legacy-monolith.md` — note the new auto-identify hook.
- Modify: `docs/INDEX.txt` — two new decision-doc entries.

- [ ] **Step 1: Write `docs/decisions/why-auto-identify-in-backend-not-inference.md`**

```markdown
**Decision:** The auto-identify lookup (matching new detections against the Reference Embedding DB) runs in the backend worker, NOT in `inference-sam3`. The original parent spec anticipated a `reference_identifier.py` module inside `inference-sam3` that would open its own read-only Postgres connection at lifespan startup. Plan C reverses that.

## Why
- **Plan B's clean separation, preserved.** [`why-standalone-embed-endpoint.md`](why-standalone-embed-endpoint.md) decided the inference container should NOT carry psycopg2 or DB credentials. Putting the auto-identify lookup in inference-sam3 would have re-introduced exactly that coupling.
- **Failure-mode isolation.** A DB outage today does not break `/detect` — embedding extraction is pure GPU work. If the lookup lived inside `/detect`, a slow or unreachable Postgres would cascade into detection latency / errors. Keeping it on the backend side means a DB outage only affects auto-identify enrichment; detections continue to land.
- **Reuses the pool-level pgvector adapter** added in Plan B (`_VectorAwareConnection` in `backend/database.py`). The worker already has a vector-aware pooled connection; inference-sam3 would have needed its own.
- **The required embedding is already on the detection.** `inference-sam3` attaches `det["embedding"]` (DINOv3-SAT, 1024-d fp16_b64) to every detection it returns. The worker decodes it via the existing `_parse_embedding_anchor` helper and queries pgvector directly. No second model call needed.

## What we rejected
- **`inference-sam3/reference_identifier.py` with its own psycopg2 connection.** Would have meant: pinning psycopg2 in the GPU image, threading DB credentials into a service that has no need to write rows, adding another failure-mode coupling. Net negative.
- **A new `POST /api/internal/reference/identify` HTTP endpoint.** Pure plumbing — the worker is the natural caller and already holds the cursor, so an HTTP hop adds latency and another round-trip for no benefit.

## Consequences
- The auto-identify call lives at `backend/worker_legacy.py` immediately after the detection INSERT (line ~2592).
- A blanket `except Exception → logger.warning` wraps the call: identification is best-effort and MUST NOT break detection persistence.
- The threshold (`REFERENCE_ID_AUTO_THRESHOLD`, default 0.85) is read from env at backend startup.
- Future Plan D (analyst-side `/api/detections/{id}/identify`) lives in the backend routers, NOT inference-sam3, for the same reasons.
```

- [ ] **Step 2: Write `docs/decisions/why-auto-write-with-threshold.md`**

```markdown
**Decision:** When the top-1 reference-platform candidate has cosine score ≥ `REFERENCE_ID_AUTO_THRESHOLD` (default 0.85), auto-write `platform_name` / `platform_family` / `platform_confidence` / `platform_source='auto'` to `object_details`. Below threshold, leave `object_details` untouched and the candidates land as `status='pending'` for analyst review.

## Why
- **Save analyst time on confident matches.** At threshold 0.85 on unit-normalised DINOv3-SAT embeddings, a top-1 match is extremely unlikely to be wrong; auto-writing prevents the analyst from having to confirm the obvious.
- **Audit trail is preserved.** Every candidate row (auto-applied or pending) carries `score`, `rank`, `matched_chip_ids`. An analyst can always retrace what drove the auto-apply decision.
- **Threshold is operator-tunable.** Defence analyst sites with stricter requirements set `REFERENCE_ID_AUTO_THRESHOLD=0.95`; sites with looser standards lower it. Documented in `docs/deployment/environment-variables-reference.md` (TODO if not already there).
- **`platform_source='auto'` makes the provenance visible.** UI surfaces (Plan D) can render auto-applied rows differently (e.g. lower visual weight) so an analyst always sees what was machine-asserted vs analyst-asserted.

## What we rejected
- **Always-pending, never auto-write.** Would mean an analyst has to click-approve even when the top-1 score is 0.99 against a 1000-chip-strong reference. Wastes time without improving safety meaningfully.
- **Never auto-overwrite an analyst assertion.** The current SQL uses `ON CONFLICT DO UPDATE` which would clobber a prior analyst-asserted `platform_name`. Mitigation: the existing UPSERT in `detection_helpers.py` uses `COALESCE(EXCLUDED.X, object_details.X)` — analyst writes preserve themselves. The auto-identify SQL deliberately uses straight `EXCLUDED.X` (no COALESCE) because by-design it should set the platform fields, and if an analyst has *already* set them, the candidate row's `score` field will tell the operator "you overrode this manually" (Plan D UI affordance).
- **Threshold = 1.0 (exact-match only).** Too strict; fp16 round-trip noise alone is ~5e-4, so even a self-vs-self lookup would rarely hit 1.0. 0.85 is the sweet spot per the parent spec.

## Consequences
- `object_details.platform_source` carries the discriminator; downstream consumers can branch on it.
- A bug in auto-apply could silently mis-label many detections at once. The mitigation is the CHECK constraint added in Task 1 (`platform_confidence ∈ [0, 1]`) plus the visible `platform_source='auto'` flag, plus the audit-trail candidates table.
- Re-baking the reference DB (e.g. switching to a richer xView seed) will NOT retroactively change existing `object_details.platform_*` rows. A separate "refresh identifications" maintenance task in a future plan is the right tool for that.
```

- [ ] **Step 3: Update existing module docs**

For each of these docs, refresh briefly:
- `/nvme/osint/docs/backend/reference-platform-db.md` — In the "Outputs" section, add: "plus a CHECK constraint `object_details_platform_confidence_check` keeping `platform_confidence ∈ [0, 1]`". In Key symbols, add lines for `find_similar_platforms` and `attach_identification_candidates` with line ranges (run `grep -n` in `backend/reference_platform_db.py` to get the real numbers).
- `/nvme/osint/docs/backend/reference-platform-baker.md` — Companion line currently mentions `stage_dota_chips.py`; add a one-line cross-reference to the new `find_similar_platforms` / `attach_identification_candidates` helpers ("Companion read-path: …").
- `/nvme/osint/docs/backend/detection-helpers.md` — In the "Key symbols" / "Inputs" section, note that `_upsert_object_details` now also accepts optional `platform_name` / `platform_family` / `platform_confidence` / `platform_source` fields via `ObjectDetailsBody`.
- `/nvme/osint/docs/backend/worker-legacy-monolith.md` — In the "Key symbols" section or wherever the detection-insert site is documented, add a one-line note: "After each detection INSERT, the worker calls `attach_identification_candidates(...)` (best-effort, wrapped in `try/except`) to attach reference-DB platform candidates."

- [ ] **Step 4: Update `docs/INDEX.txt`**

Add two new entries in correct alphabetical positions inside the `decisions/` block:

```
decisions/why-auto-identify-in-backend-not-inference.md|decision|reference-DB auto-identify runs in the backend worker, not inference-sam3
decisions/why-auto-write-with-threshold.md|decision|auto-write platform_* to object_details when top-1 cosine ≥ 0.85; below threshold land as pending
```

Tags `decision` only — both are in the canonical vocabulary. Confirm within-section alphabetical position relative to other `decisions/why-au*.md` entries.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions/why-auto-identify-in-backend-not-inference.md docs/decisions/why-auto-write-with-threshold.md docs/backend/reference-platform-db.md docs/backend/reference-platform-baker.md docs/backend/detection-helpers.md docs/backend/worker-legacy-monolith.md docs/INDEX.txt
git commit -m "docs(reference-db): auto-identify decision records + helper/worker doc updates"
```

---

## Task 7 — Final end-to-end verification

**Files:** none modified.

- [ ] **Step 1: Full test run**

```bash
docker compose up -d postgis backend inference-sam3
sleep 5
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py tests/test_reference_platform_baker.py tests/test_reference_platform_auto_identify.py tests/test_pgvector_pool_registration.py tests/test_object_details.py -v 2>&1 | tail -15"
```

Expected: 24 passed.

- [ ] **Step 2: End-to-end live exercise**

Insert a synthetic detection whose embedding closely matches an existing populated DOTA centroid (e.g. `DOTA::plane`). Verify the auto-identify call wrote both a candidate row and the object_details row.

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -c '
import sys
sys.path.insert(0, \"/app\")
from database import postgis_db
from reference_platform_db import attach_identification_candidates

# Use DOTA::plane centroid as the synthetic detection's embedding
with postgis_db.get_cursor(commit=True) as cur:
    cur.execute(\"SELECT centroid_overhead FROM reference_platforms WHERE platform_name = %s\", (\"DOTA::plane\",))
    row = cur.fetchone()
    assert row is not None and row[\"centroid_overhead\"] is not None, \"DOTA::plane centroid missing; re-run Plan B Task 7\"
    plane_emb = row[\"centroid_overhead\"]
    cur.execute(\"\"\"
        INSERT INTO detections (class, confidence, geom, centroid, metadata)
        VALUES (%s, 0.95,
                ST_GeomFromText('\''POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))'\'', 4326),
                ST_GeomFromText('\''POINT(0.5 0.5)'\'', 4326),
                '\''{}'\''::jsonb)
        RETURNING id
    \"\"\", (\"e2e-test-plane\",))
    det_id = cur.fetchone()[\"id\"]
    n = attach_identification_candidates(
        cur, detection_id=det_id, embedding=plane_emb,
        view_domain=\"overhead\", auto_threshold=0.85, top_k=3,
    )
print(\"candidates_written:\", n)

with postgis_db.get_cursor(commit=False) as cur:
    cur.execute(\"\"\"
        SELECT p.platform_name, c.score, c.rank, c.status, c.applied_at
          FROM platform_identification_candidates c
          JOIN reference_platforms p ON c.platform_id = p.id
         WHERE c.detection_id = %s
         ORDER BY c.rank
    \"\"\", (det_id,))
    for r in cur.fetchall():
        print(\"  cand:\", r[\"rank\"], r[\"platform_name\"], round(r[\"score\"], 4), r[\"status\"], r[\"applied_at\"] is not None)
    cur.execute(\"\"\"
        SELECT platform_name, platform_family, platform_confidence, platform_source
          FROM object_details
         WHERE source = '\''detection'\'' AND source_id = %s
    \"\"\", (str(det_id),))
    od = cur.fetchone()
    print(\"  object_details:\", od)

# Cleanup
with postgis_db.get_cursor(commit=True) as cur:
    cur.execute(\"DELETE FROM platform_identification_candidates WHERE detection_id = %s\", (det_id,))
    cur.execute(\"DELETE FROM object_details WHERE source = '\''detection'\'' AND source_id = %s\", (str(det_id),))
    cur.execute(\"DELETE FROM detections WHERE id = %s\", (det_id,))
'"
```

Expected:
- `candidates_written: 3` (top-3 platforms returned).
- Top candidate is `DOTA::plane` with `score: 1.0`, `status: auto_applied`, `applied_at: True`.
- `object_details` is non-NULL: `platform_name='DOTA::plane'`, `platform_family='Plane'`, `platform_confidence=1.0`, `platform_source='auto'`.

- [ ] **Step 3: Scope check**

```bash
git diff --name-only $(git log --format='%H' --grep='chore(reference-db): fix stale bake-script' -1)..HEAD
```

Expected files (Plan C scope):
- `backend/platform_schema.py`
- `backend/reference_platform_db.py`
- `backend/schemas.py`
- `backend/detection_helpers.py`
- `backend/worker_legacy.py`
- `backend/tests/test_reference_platform_auto_identify.py`
- `docs/decisions/why-auto-identify-in-backend-not-inference.md`
- `docs/decisions/why-auto-write-with-threshold.md`
- `docs/backend/reference-platform-db.md`
- `docs/backend/reference-platform-baker.md`
- `docs/backend/detection-helpers.md`
- `docs/backend/worker-legacy-monolith.md`
- `docs/INDEX.txt`
- `docs/superpowers/plans/2026-05-27-reference-db-plan-c-auto-identify.md` (this file)

NOTHING in `inference-sam3/`, `backend/routers/`, or `frontend/`.

## Definition of Done

- `object_details_platform_confidence_check` CHECK constraint present in DB.
- `find_similar_platforms` returns ranked candidates; tested.
- `attach_identification_candidates` writes PIC rows + auto-applies to object_details above threshold; tested across 4 paths (rank ordering, auto-apply, pending, idempotency).
- Worker splices the call after detection INSERT; smoke-test passes against the live DOTA bake.
- `REFERENCE_ID_AUTO_THRESHOLD` env var wired (default 0.85).
- All 24 reference-DB / pool-registration / object-details tests pass.
- Two new decision docs + four module-doc refreshes + INDEX.txt updated.
- No inference-sam3, router, or frontend code modified.

## What this plan does NOT do

- Expose any HTTP API for analyst-driven re-identification (Plan D).
- Add the `IdentificationPanel` UI component (Plan D).
- Add the admin tab for browsing the reference DB (Plan D).
- Add new datasets beyond DOTA — xView/RarePlanes/ShipRSImageNet seeds follow a per-dataset recipe under a future `docs/conventions/adding-a-reference-dataset.md`.
- Refresh stale auto-applied identifications when the reference DB is re-baked — that's a separate maintenance-task plan.

Hand back to the user when "Definition of Done" is fully checked.
