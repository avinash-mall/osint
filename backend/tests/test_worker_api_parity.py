"""Worker public-API + Celery routing parity guard.

The worker code was split out of the 6.2k-line ``worker_legacy`` monolith into
the ``worker`` package (see docs/decisions/why-worker-package-split-2026-06-16.md).
This test is the safety net for that split: it pins, against a committed baseline
captured BEFORE the split, the two contracts a no-GPU refactor must not silently
break —

  1. the exact set of Celery task routing keys (``celery_app.tasks``), and
  2. the public import surface of the ``worker`` package (every name external
     code does ``from worker import X`` on must still resolve).

It also checks the handful of names that callers still import from the
``worker_legacy`` compatibility shim. (The broader ``from worker_legacy import X``
surface is covered for free: any dropped name makes the importing test error at
collection.)

Regenerate the baseline only on a DELIBERATE public-API change, by re-running the
capture snippet in docs/decisions/why-worker-package-split-2026-06-16.md and
committing the new _worker_api_baseline.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

BASELINE_PATH = Path(__file__).with_name("_worker_api_baseline.json")

# Names callers still pull from the worker_legacy shim (static `from worker_legacy
# import …` in backend/scripts + tests, and `worker_legacy.X` attribute access).
# Keep in sync with the shim's re-exports; a miss here also surfaces as an import
# error in the owning test, so this is a fast, explicit second line of defence.
CRITICAL_LEGACY_NAMES = (
    "celery_app",
    # postprocess
    "_DetectionDedupeIndex",
    "_WeightedBoxFusionIndex",
    "_geo_stale_after_merge",
    "_rederive_geo_from_pixel_bbox",
    # dispatch / grid
    "DEFAULT_INFERENCE_CHIP_SIZE",
    "DEFAULT_INFERENCE_OVERLAP",
    "plan_inference_grid",
    # imagery
    "clear_existing_detections",
    # graph
    "_near_radius_for_kind",
    "_parse_embedding_anchor",
    "tick_aggregate_entity_embeddings",
    "tick_near_builder",
    "tick_propose_entities",
    "tick_repeat_detector",
    # maintenance
    "seed_reference_db",
)


def _load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text())


def _current() -> dict:
    import worker
    import worker_legacy
    from worker_legacy import celery_app

    def names(mod):
        return sorted(n for n in dir(mod) if not n.startswith("__"))

    return {
        "tasks": sorted(celery_app.tasks.keys()),
        "worker": names(worker),
        "worker_legacy": names(worker_legacy),
    }


def test_celery_task_registry_unchanged():
    """The full set of Celery routing keys must be byte-identical — a dropped or
    renamed task silently breaks beat scheduling and message routing."""
    base = set(_load_baseline()["tasks"])
    cur = set(_current()["tasks"])
    missing = sorted(base - cur)
    added = sorted(cur - base)
    assert not missing, f"Celery tasks DROPPED by the split: {missing}"
    assert not added, f"Unexpected new Celery tasks (update baseline if intended): {added}"


def test_worker_package_public_surface_preserved():
    """Every public name the package exported before the split must still resolve
    via `from worker import X` — guards the routers/main.py import contract."""
    base = set(_load_baseline()["worker"])
    import worker

    cur = {n for n in dir(worker) if not n.startswith("__")}
    dropped = sorted(base - cur)
    assert not dropped, f"worker package public names DROPPED: {dropped}"


def test_worker_legacy_shim_backcompat():
    """The names callers still import from the worker_legacy shim must resolve."""
    import worker_legacy

    missing = [n for n in CRITICAL_LEGACY_NAMES if not hasattr(worker_legacy, n)]
    assert not missing, f"worker_legacy shim missing back-compat names: {missing}"


def test_all_worker_tasks_present():
    """Sanity: the 22 worker.* tasks are all registered (subset of the exact check
    above, but fails with a clearer message if a whole task module fails to import)."""
    base = sorted(t for t in _load_baseline()["tasks"] if t.startswith("worker."))
    cur = set(_current()["tasks"])
    missing = [t for t in base if t not in cur]
    assert not missing, f"worker.* tasks not registered: {missing}"
