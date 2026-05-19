"""worker.postprocess — dedupe / NMS / candidate-link scoring.

Thin facade over ``worker_legacy``. The dedupe helpers are
implementation-private to the legacy module; the symbols below are the
ones the rest of the codebase consumes (most are internal).
"""

from __future__ import annotations

# These helpers are referenced from inference / inference test paths.
# Import lazily — they exist in worker_legacy but only certain names are
# guaranteed public. Add specific names here as callers need them.
from worker_legacy import (
    clear_existing_detections,
    store_detections,
)


__all__ = [
    "clear_existing_detections",
    "store_detections",
]
