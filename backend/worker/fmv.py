"""worker.fmv — FMV NDJSON consumer + track persistence.

Thin facade over ``worker_legacy.process_fmv``.
"""

from __future__ import annotations

from worker_legacy import process_fmv


__all__ = ["process_fmv"]
