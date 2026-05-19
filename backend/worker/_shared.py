"""worker._shared — env helpers, upload-job DB rows, progress reporter.

Thin facade over ``worker_legacy``. Bodies are unchanged; this module
exists so that callers can ``from worker._shared import env_int``
matching the package layout the senior-review design specified.
"""

from __future__ import annotations

from worker_legacy import (
    env_bool,
    env_float,
    env_int,
    ensure_worker_imagery_schema,
    get_upload_job,
    report_progress,
    update_upload_job,
)


__all__ = [
    "env_bool",
    "env_float",
    "env_int",
    "ensure_worker_imagery_schema",
    "get_upload_job",
    "report_progress",
    "update_upload_job",
]
