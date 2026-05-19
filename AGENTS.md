# Sentinel — Agent Entry Point

Sentinel is an open-source GEOINT exploitation platform. Single FastAPI backend + Celery worker + an `inference-sam3` ML service that bundles SAM 3 / SAM 3.1 + YOLOE-26x-seg + DINOv3-SAT + Prithvi-EO-2.0 + TerraMind v1 + DOTA-OBB + Grounding-DINO. Frontend is React 19 + Vite 8. Everything ships as a self-contained Docker Compose stack that can run air-gapped.

## Mandatory workflow

**Before any task:**
1. Read [docs/INDEX.txt](docs/INDEX.txt) — full doc tree, 15 KB.
2. Read the module docs for the files you'll touch.
3. Read the relevant decision docs (`docs/decisions/`) and convention recipe (`docs/conventions/`).

**After every task — before declaring it done:**
1. Update the module doc for every file you modified — same six-section template (Path / Lines / Depends on / Purpose / Why this design / Key symbols / Inputs / Outputs / Failure modes / Cross-references).
2. Keep `file.py#Lx-Ly` line ranges current; refresh the `**Lines:** ~NNN` header if the file grew or shrank >10%.
3. Add a `docs/decisions/<name>.md` for any architectural choice or removal you made.
4. Update [docs/INDEX.txt](docs/INDEX.txt) for new/renamed docs (one line per doc, sorted by path, tags from the fixed vocabulary).
5. Fix cross-references — grep for the affected module name in `docs/`.

Full rules: **[docs/conventions/documentation-workflow.md](docs/conventions/documentation-workflow.md)**. Not optional.

## Read first

- **One-line index of every doc:** [docs/INDEX.txt](docs/INDEX.txt)
- **System topology:** [docs/architecture/system-overview.md](docs/architecture/system-overview.md)
- **API surface (~100 routes):** [docs/backend/api-routes-reference.md](docs/backend/api-routes-reference.md)
- **Inference service:** [docs/inference/service-overview.md](docs/inference/service-overview.md)
- **Why things are the way they are:** [docs/decisions/](docs/decisions/) — start with [why-open-vocabulary.md](docs/decisions/why-open-vocabulary.md) and [why-yoloe-replaced-amg.md](docs/decisions/why-yoloe-replaced-amg.md)
- **Recipes for adding new things:** [docs/conventions/](docs/conventions/) — `adding-a-new-detection-model.md`, `-ontology-object.md`, `-router.md`, `-celery-task.md`, `-admin-tab.md`

## Hard rules

1. **Do not write to runtime data dirs.** Treat as read-only on the dev host: `/data/*`, `bench/`, `assets/static/basemap/`, `inference-sam3/yolo*.pt`, `inference-sam3/yoloe-*.pt`, `inference-sam3/mobileclip2_b.ts`. These are populated at build time or by long-running pipelines.
2. **Run `python scripts/configure_host.py` before changing GPU env.** It writes a `SENTINEL GENERATED GPU CONFIG` block into `.env` based on `nvidia-smi`. Do **not** hand-edit that block or copy it across machines — see [docs/deployment/gpu-profile-detection.md](docs/deployment/gpu-profile-detection.md).
3. **Every new router registers in [backend/main.py](backend/main.py).** Routers add their own prefix; the session middleware in `main.py` gates all mutating verbs automatically.
4. **Ontology is the canonical prompt source.** Do not hard-code class lists. Inference fetches `/api/ontology/default-prompts?sensor=...` with a 30s cache; SIGHUP forces refresh. See [docs/backend/ontology-system.md](docs/backend/ontology-system.md).
5. **Open-vocabulary policy.** Every SAM 3 / open-set label is first-class. Confidence floors only via `GLOBAL_CONFIDENCE_FLOOR` and `PER_CLASS_CONFIDENCE_OVERRIDES` — never delete a class. See [docs/decisions/why-open-vocabulary.md](docs/decisions/why-open-vocabulary.md).
6. **Celery task names are routing identity.** When refactoring a task out of `backend/worker_legacy.py`, preserve `@celery_app.task(name="worker.xxx")` exactly — Celery routes by explicit name, not Python FQN. See [docs/backend/worker-package-facade.md](docs/backend/worker-package-facade.md).
7. **No `--no-verify`, no force-push, no `git config` edits.** Standard repo hygiene.

## Doc shape (you can pattern-match these)

```
**Path:** repo-relative link
**Lines:** ~NNN
**Depends on:** module list + env

## Purpose
## Why this design
## Key symbols    (with file.py#Lx-Ly ranges)
## Inputs / Outputs
## Failure modes
## Cross-references
```

`grep -A1 "## Why this design" docs/**/*.md` is a fast way to extract architecture.

## When in doubt

Open [docs/INDEX.txt](docs/INDEX.txt). 125+ entries, each ≤100 chars, sorted by path.
