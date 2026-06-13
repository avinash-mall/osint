"""worker.imagery — satellite imagery pipeline.

Thin facade over ``worker_legacy``. The actual Celery tasks
(``process_satellite_imagery``) are
defined in ``worker_legacy`` with explicit ``name="worker.xxx"``
arguments, so Celery routing keys remain identical.
"""

from __future__ import annotations

from worker_legacy import (
    clear_existing_detections,
    detection_class_summary,
    ensure_cog,
    get_raster_footprint,
    process_satellite_imagery,
    resolve_input_path,
    run_sar_cfar_for_pass,
    slice_and_infer,
    store_detections,
)


__all__ = [
    "clear_existing_detections",
    "detection_class_summary",
    "ensure_cog",
    "get_raster_footprint",
    "process_satellite_imagery",
    "resolve_input_path",
    "run_sar_cfar_for_pass",
    "slice_and_infer",
    "store_detections",
]
