# Why `worker_legacy.py` Stays Monolithic (For Now)

**Status:** SUPERSEDED (2026-06-16) by [why-worker-package-split-2026-06-16.md](why-worker-package-split-2026-06-16.md) — the monolith was split into the `worker` package. The original reasoning is kept below for history.

## Decision

[backend/worker_legacy.py](../../backend/worker_legacy.py) is ~3650 lines, the largest single file in the repo. **Not** being split incrementally. Instead, a thin package [backend/worker/](../../backend/worker/) re-exports it.

## Why

- **Celery task names are routing identity** — every `@celery_app.task(name="worker.xxx")` is referenced by callers as `worker.process_satellite_imagery`, `worker.process_fmv`, etc. Moving the function changes the import path — but as long as the name argument stays the same and the function is re-exported, Celery routes by explicit name, not Python FQN. The package facade preserves all names.
- **Coverage is in the integration paths** — unit tests in `backend/tests/` cover small modules (auth, ontology, candidate linking); `worker_legacy.py` is covered mostly by end-to-end runs and bench scripts. Splitting it into 12 modules without a test net would lose that integration coverage by accident.
- **No active feature work in there** — the file is in maintenance; new features go in routers and helpers, the worker tasks orchestrate. Splitting is pure refactor, not feature-driven.
- **Performance** — a single import of `worker_legacy` is faster than 12 module imports at Celery worker startup. Trivial in absolute terms but real for cold restarts.

## The package facade pattern

[backend/worker/__init__.py](../../backend/worker/__init__.py):

```python
from worker_legacy import *  # re-export everything for `from worker import process_fmv`
```

[backend/worker/dispatch.py](../../backend/worker/dispatch.py), `imagery.py`, `fmv.py`, `postprocess.py` each re-export a curated subset for new code wanting narrow imports. They contain **no logic** — only re-exports and one or two thin wrappers that constants live in.

So: new code can do `from worker.imagery import chip_plan` (clean) while existing callers keep `from worker_legacy import chip_plan` (legacy-friendly). The legacy import path is removed only after callers migrate.

## When to split

The split becomes worth it when:
1. A new feature needs to touch a specific concern (e.g. SAR pipeline) and the relevant ~400 lines are stable enough to extract.
2. A bug investigation needs to instrument one concern in isolation.

Each extraction should:
- Move functions one at a time.
- Keep `@celery_app.task(name=...)` argument **literally identical**.
- Add a re-export shim in `worker_legacy.py` for one release before deletion.

## Cross-references

- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [backend/worker-package-facade.md](../backend/worker-package-facade.md)
- [conventions/adding-a-new-celery-task.md](../conventions/adding-a-new-celery-task.md)
