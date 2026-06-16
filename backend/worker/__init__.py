"""Sentinel worker package.

The 6.2k-line ``worker_legacy`` monolith was split into concern modules
(see docs/decisions/why-worker-package-split-2026-06-16.md). Submodules are
imported below in dependency order so every ``@celery_app.task`` decorator runs
(registering its routing key) and the full public surface is re-exported. Callers
use ``from worker import X``; ``worker_legacy`` remains a thin compatibility shim.
"""

from __future__ import annotations

from worker.config import *  # noqa: F401,F403
from worker.app import *  # noqa: F401,F403  (celery_app, worker_process_init)
from worker._shared import *  # noqa: F401,F403
from worker.dispatch import *  # noqa: F401,F403
from worker.postprocess import *  # noqa: F401,F403
from worker.graph import *  # noqa: F401,F403  (before fmv: consolidate_fmv -> project_fmv_to_graph)
from worker.fmv import *  # noqa: F401,F403
from worker.maintenance import *  # noqa: F401,F403
from worker.imagery import *  # noqa: F401,F403

__all__ = [n for n in dir() if not n.startswith("__")]
