"""Sentinel worker package.

Replaces the monolithic ``backend/worker.py`` (161 KB). The actual Celery
task bodies still live in ``worker_legacy`` so we don't have to move ~3500
lines of well-tested code; this package re-exports them as a structured
namespace:

  worker._shared        env helpers, upload-job DB rows, progress reporter
  worker.dispatch       chip-pool + SAM3 HTTP client + chip encoder
  worker.postprocess    dedupe / NMS / candidate-link scoring
  worker.imagery        COG conversion, slice_and_infer, satellite tasks
  worker.fmv            FMV NDJSON consumer + track persistence

Every ``@celery_app.task`` decorator in the legacy module declares an
explicit ``name="worker.xxx"`` — Celery routes by that name, not by Python
FQN — so moving the file from ``worker.py`` to a package preserves all
routing keys without re-decorating.

Public re-exports below cover every symbol older code imported from the
flat ``worker`` module (``celery_app``, ``process_fmv``,
``process_satellite_imagery``, etc.).
"""

from __future__ import annotations

# Importing legacy executes every @celery_app.task decorator, registering
# tasks against the singleton Celery app it constructs at line 160.
from worker_legacy import *  # noqa: F401,F403
from worker_legacy import (  # noqa: F401  — explicit names for IDE completion
    celery_app,
    consolidate_fmv,
    process_fmv,
    process_satellite_imagery,
    project_documents_to_graph,
    project_fmv_to_graph,
    project_label_of_edges,
    project_observations_to_graph,
    project_ontology_to_graph,
    project_unknown_labels,
    tick_aggregate_entity_embeddings,
    tick_entity_resimilarity,
    tick_near_builder,
    tick_propose_entities,
    tick_repeat_detector,
    transcribe_audio,
    train_model,
)

# Underscore-prefixed test fixtures + helper classes the test suite imports
# directly via `from worker import _DetectionDedupeIndex`. `from X import *`
# does not propagate underscore-prefixed names, so each must be listed.
from worker_legacy import (  # noqa: F401
    _calibration_tag_for_detection,
    _DetectionDedupeIndex,
    _emit_chip_payload,
    _WeightedBoxFusionIndex,
)
