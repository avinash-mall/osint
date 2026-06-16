"""Backward-compatibility shim.

The worker code now lives in the ``worker`` package (split from this 6.2k-line
monolith on 2026-06-16; see docs/decisions/why-worker-package-split-2026-06-16.md).
``from worker_legacy import X`` and ``worker_legacy.X`` still resolve for every
previously-exported name. New code should import from ``worker`` or its submodules.

Note for tests: monkeypatching a moved helper must target its OWNING module
(e.g. ``worker.dispatch._wait_for_inference_healthy``), not this shim — a function
resolves its globals in the module where it is defined, not here.
"""

from worker import *  # noqa: F401,F403
