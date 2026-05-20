"""worker.fmv — FMV NDJSON consumer + track persistence + consolidation.

Thin facade over ``worker_legacy.process_fmv`` and
``worker_legacy.consolidate_fmv`` (post-inference track consolidation —
see ``backend/fmv_tracker.py``).
"""

from __future__ import annotations

from worker_legacy import consolidate_fmv, process_fmv


__all__ = ["process_fmv", "consolidate_fmv"]
