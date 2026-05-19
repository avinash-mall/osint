"""worker.dispatch — chip-pool + SAM3 HTTP client + chip encoder.

Thin facade over ``worker_legacy``.
"""

from __future__ import annotations

from worker_legacy import (
    INFERENCE_CHIP_CONCURRENCY,
    INFERENCE_CHIP_SPOOL_MAX_BYTES,
    INFERENCE_CHIP_TIMEOUT_S,
    INFERENCE_MAX_PENDING_CHIPS,
    INFERENCE_SAM3_URL,
    chip_to_uint8_rgb,
)


__all__ = [
    "INFERENCE_CHIP_CONCURRENCY",
    "INFERENCE_CHIP_SPOOL_MAX_BYTES",
    "INFERENCE_CHIP_TIMEOUT_S",
    "INFERENCE_MAX_PENDING_CHIPS",
    "INFERENCE_SAM3_URL",
    "chip_to_uint8_rgb",
]
