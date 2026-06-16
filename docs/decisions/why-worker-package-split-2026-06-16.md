# Why worker_legacy.py Was Split Into the worker Package

**Date:** 2026-06-16
**Status:** Accepted (supersedes [why-worker-legacy-monolith-kept.md](why-worker-legacy-monolith-kept.md))

## Context

`backend/worker_legacy.py` had grown to 6,235 lines holding every Celery task plus their
helpers. A prior decision ([why-worker-legacy-monolith-kept.md](why-worker-legacy-monolith-kept.md))
kept it monolithic because Celery task `name=` routing keys are identity and the large
orchestrators (`slice_and_infer` ~909 lines, `process_satellite_imagery` ~462) had only
end-to-end coverage — so a careless split risked breaking routing or silently dropping
coverage. A follow-up review asked for the split to be done properly.

## Decision

Split the monolith by concern into the `worker` package (module map:
[worker-package-facade.md](../backend/worker-package-facade.md)). Direction inverted:
`worker/*` now hold the real code and `worker_legacy.py` is a 13-line compatibility shim
(`from worker import *`). Code moved **verbatim** — no logic edits — preserving every
`@celery_app.task(name="worker.xxx")` routing key.

Foundation pattern: `worker/config.py` carries the monolith's import preamble + all env
constants + loaders and re-exports them via a `dir()`-based `__all__`; every module does
`from worker.config import *` to inherit that namespace (so each moved function's free
variables — `np`, `requests`, constants — resolve). `__init__.py` imports submodules in
dependency order (config → app → leaf helpers → graph → fmv → maintenance → imagery) so
every task decorator runs and the full surface is re-exported.

## How it was verified without a GPU

The orchestrators can't be exercised without a GPU + real imagery, so the split was guarded
structurally:

1. **Parity harness** ([test_worker_api_parity.py](../../backend/tests/test_worker_api_parity.py)
   + committed [`_worker_api_baseline.json`](../../backend/tests/_worker_api_baseline.json)):
   the exact set of Celery routing keys (22 `worker.*` tasks) and the `worker` package's public
   import surface must equal the pre-split baseline.
2. **LOAD_GLOBAL disassembly scan**: every moved function **and task body** is disassembled and
   each `LOAD_GLOBAL` operand asserted resolvable in its module namespace — proving name
   resolution without executing the code. (This caught 8 cross-module references the star-import
   didn't cover: `geometry.iou_xyxy`, `events.publish_event`, `worker.graph._parse_embedding_anchor`,
   `worker.graph.project_fmv_to_graph`.)
3. **Full unit suite** (in-container): 426 passed, the same 3 pre-existing environmental failures,
   zero new failures. Live worker + worker_beat boot healthy with all 22 tasks and the beat
   schedule intact.

## Consequence: monkeypatching targets the owning module

A function resolves its globals where it is **defined**, not in the `worker_legacy` shim. Tests
that patched `worker_legacy.X` (a helper/constant/`postgis_db`) were retargeted to the module that
now owns it — `import worker.dispatch as worker` (restart-retry), `import worker.graph as worker_legacy`
(graph tasks), `import worker.maintenance as worker_legacy` (seed). Read-only `from worker_legacy
import X` and `worker_legacy.X()` calls keep working through the shim unchanged.

## Cross-references

- [why-worker-legacy-monolith-kept.md](why-worker-legacy-monolith-kept.md) (superseded)
- [../backend/worker-package-facade.md](../backend/worker-package-facade.md)
- [../backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [../conventions/adding-a-new-celery-task.md](../conventions/adding-a-new-celery-task.md)
