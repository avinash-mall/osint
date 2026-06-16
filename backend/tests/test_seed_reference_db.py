"""Integration tests for the worker.seed_reference_db Celery task and the
POST /api/admin/reference/seed admin endpoint.

The task is exercised by calling it as a plain function with a stub /embed
backend and a tiny synthetic chip tree under tmp_path. The admin endpoint
test only verifies the enqueue path — the actual task execution is covered
by the unit tests above.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# scripts/ lives at <repo>/backend/scripts on the host and at /app/scripts in
# the backend container. Try both so the same test file runs in either context.
_HERE = Path(__file__).resolve()
for _cand in (BACKEND_DIR / "scripts", _HERE.parents[1] / "scripts"):
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))
        break
SCRIPTS_DIR = next(
    (c for c in (BACKEND_DIR / "scripts", _HERE.parents[1] / "scripts") if c.is_dir()),
    BACKEND_DIR / "scripts",
)
SEED_PATH = SCRIPTS_DIR / "seeds" / "reference_platforms.seed.json"


@pytest.fixture(scope="module", autouse=True)
def _setup_env():
    os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-replace-1234567890abcdef")
    os.environ.setdefault("ADMIN_USERNAME", "test-admin")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")


def _make_corpora_tree(tmp_path: Path) -> Path:
    """Synthesize a /opt/reference-corpora/-shaped tree with one DOTA-like dataset."""
    root = tmp_path / "reference-corpora"
    dota = root / "dota"
    (dota / "plane").mkdir(parents=True)
    Image.new("RGB", (96, 96), color=(80, 90, 100)).save(dota / "plane" / "P0001__plane.png")
    Image.new("RGB", (96, 96), color=(120, 130, 140)).save(dota / "plane" / "P0002__plane.png")
    manifest = {
        "version": 1,
        "source_dataset": "dota",
        "chip_count": 2,
        "chips": [
            {"chip_path": "plane/P0001__plane.png", "class_name": "plane",
             "license_spdx": "CC-BY-4.0", "attribution": "DOTA team", "sha256": ""},
            {"chip_path": "plane/P0002__plane.png", "class_name": "plane",
             "license_spdx": "CC-BY-4.0", "attribution": "DOTA team", "sha256": ""},
        ],
    }
    (dota / "MANIFEST.json").write_text(json.dumps(manifest))
    (root / "MANIFEST.sha256").write_text("deadbeef\n")
    return root


def _stub_embed_response(monkeypatch):
    """Patch bake_reference_index._post_embed so the seed task doesn't need
    inference-sam3 at test time."""
    import bake_reference_index as bake_mod  # type: ignore

    import base64

    class _FakeResp:
        status_code = 200
        text = ""
        def __init__(self, body): self._body = body
        def json(self): return self._body

    def _fake_post_embed(url, files, timeout=60):
        # Deterministic per-call vector — just zeros with a small marker.
        vec = np.zeros(1024, dtype=np.float16)
        vec[0] = 1.0
        b64 = base64.b64encode(vec.tobytes()).decode("ascii")
        return _FakeResp({"model": "dinov3-sat", "dim": 1024, "fp16_b64": b64})

    monkeypatch.setattr(bake_mod, "_post_embed", _fake_post_embed)
    return bake_mod


def _cleanup_rows():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'dota' "
                    "AND chip_path LIKE %s", (f"%pytest-seedtask%",))
        cur.execute("DELETE FROM reference_platforms WHERE platform_name = 'DOTA::plane'")


def test_seed_reference_db_short_circuits_when_rows_present(tmp_path: Path, monkeypatch):
    """force=False + existing rows → status='skipped', no bake."""
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import upsert_reference_platform
    from database import postgis_db
    ensure_reference_platform_tables()

    # Seed one row so the count > 0 guard triggers.
    with postgis_db.get_cursor(commit=True) as cur:
        upsert_reference_platform(
            cur, platform_name="pytest-seed-guard", platform_family="GuardFam",
        )

    try:
        import worker.maintenance as worker_legacy
        result = worker_legacy.seed_reference_db(force=False)  # bind=True wrapper accepts no-self call in test
    except TypeError:
        # bind=True means the celery wrapper expects self; call the underlying.
        result = worker_legacy.seed_reference_db.run(force=False)  # type: ignore

    assert result["status"] == "skipped"
    assert result["platforms_present"] >= 1

    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM reference_platforms WHERE platform_name = 'pytest-seed-guard'")


def test_seed_reference_db_runs_full_bake_when_empty(tmp_path: Path, monkeypatch):
    """force=True → iterates baked corpora → calls bake_run per dataset → publishes events."""
    _cleanup_rows()

    corpora_root = _make_corpora_tree(tmp_path)
    runtime_root = tmp_path / "runtime-chips"
    monkeypatch.setenv("REFERENCE_CORPORA_ROOT", str(corpora_root))
    monkeypatch.setenv("REFERENCE_CHIPS_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("REFERENCE_SEED_PATH", str(SEED_PATH))

    # We need the module-level constants to re-read env. Easiest: monkey-patch.
    import worker.maintenance as worker_legacy
    monkeypatch.setattr(worker_legacy, "_REFERENCE_CORPORA_ROOT", corpora_root)
    monkeypatch.setattr(worker_legacy, "_REFERENCE_CHIPS_RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(worker_legacy, "_REFERENCE_SEED_PATH", SEED_PATH)

    _stub_embed_response(monkeypatch)

    # Capture publish_event payloads.
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(worker_legacy, "publish_event",
                        lambda topic, payload: events.append((topic, payload)))

    # Call the underlying function (bypasses Celery serialization).
    result = worker_legacy.seed_reference_db.run(force=True)  # type: ignore

    assert result["status"] == "ok"
    assert any(e[1].get("type") == "started" for e in events)
    assert any(e[1].get("type") == "done" for e in events)
    # At least one dataset ran.
    ds_progress = [e for e in events if e[1].get("type") == "dataset_progress"]
    assert ds_progress, f"no dataset_progress events: {events}"
    assert any(e[1]["dataset"] == "dota" for e in ds_progress)

    # Runtime chips got rsynced.
    assert (runtime_root / "dota" / "plane" / "P0001__plane.png").is_file()

    _cleanup_rows()


def test_admin_seed_endpoint_enqueues_task(monkeypatch):
    """POST /api/admin/reference/seed returns 200 + task_id; require_admin gate holds."""
    from fastapi.testclient import TestClient
    import main

    # Mock the celery enqueue path so we don't actually run the task.
    fake_task_id = "test-task-abc123"
    sent: list[dict] = []

    class _FakeAsyncResult:
        id = fake_task_id

    def _fake_send_task(name, kwargs=None, **_):
        sent.append({"name": name, "kwargs": kwargs or {}})
        return _FakeAsyncResult()

    from worker import celery_app
    monkeypatch.setattr(celery_app, "send_task", _fake_send_task)

    client = TestClient(main.app)
    # Login as admin
    r = client.post("/api/auth/login",
                    json={"username": os.environ["ADMIN_USERNAME"],
                          "password": os.environ["ADMIN_PASSWORD"]})
    assert r.status_code == 200, r.text

    # Trigger seed (force=true)
    r = client.post("/api/admin/reference/seed", json={"force": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"] == fake_task_id
    assert body["force"] is True
    assert body["triggered_by"] == os.environ["ADMIN_USERNAME"]

    assert len(sent) == 1
    assert sent[0]["name"] == "worker.seed_reference_db"
    assert sent[0]["kwargs"]["force"] is True


def test_admin_seed_endpoint_rejects_unauth(monkeypatch):
    """No session → 401."""
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    r = client.post("/api/admin/reference/seed", json={"force": False})
    assert r.status_code == 401, r.text
