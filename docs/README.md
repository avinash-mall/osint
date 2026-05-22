# Sentinel Documentation

**Agent-first** doc tree. Every module has one short doc: path, line counts, dependencies, key symbols (with `file.py#Lx-Ly` ranges), failure modes, cross-references. Reading is cheap and pattern-extractable.

## Where to start

- **Workflow every agent must follow** (read-before / update-after): [conventions/documentation-workflow.md](conventions/documentation-workflow.md).
- **Single-screen overview** of every doc: [INDEX.txt](INDEX.txt) (pipe-delimited, ~15 KB, sorted by path).
- **Architectural identity:** [architecture/system-overview.md](architecture/system-overview.md).
- **API surface:** [backend/api-routes-reference.md](backend/api-routes-reference.md).
- **Inference service entry:** [inference/service-overview.md](inference/service-overview.md).
- **Why this design:** [decisions/](decisions/) — every load-bearing trade-off has its own file.
- **Change recipes:** [conventions/](conventions/) — adding a model, ontology object, router, Celery task, admin tab.

## Layout

| Section | Contents |
|---|---|
| [architecture/](architecture/) | System topology, data flows, component boundaries |
| [backend/](backend/) | One doc per `backend/*.py` module |
| [backend-routers/](backend-routers/) | One doc per `backend/routers/*.py` (the public `/api/*` surface) |
| [inference/](inference/) | The `inference-sam3` service: runners, fusion, gates, profile pool |
| [frontend/](frontend/) | React workspaces, panels, hooks, utils |
| [deployment/](deployment/) | docker-compose, nginx, offline air-gap, GPU profiles, env vars |
| [operations/](operations/) | Day-to-day workflows: ingest, ontology edit, candidate-link approval, LDAP |
| [decisions/](decisions/) | Why things are the way they are; also why removed things were removed |
| [testing/](testing/) | Test layouts, benchmark harness, fixtures |
| [benchmarks/](benchmarks/) | Raw evaluation reports referenced by the README |
| [scripts/](scripts/) | What every script under `scripts/` and `backend/scripts/` does |
| [conventions/](conventions/) | Coding style and change-recipe playbooks |

## Conventions inside every doc

```markdown
**Path:** [backend/foo.py](../../backend/foo.py)
**Lines:** ~NNN
**Depends on:** ...

## Purpose
One sentence.

## Why this design
Architectural intent + past incident if any.

## Key symbols
- [`function`](../../backend/foo.py#L42-L58) — one line.

## Inputs / Outputs
Schema or signature. Link to schemas.py, not paste.

## Failure modes
Raise / fallback / log.

## Cross-references
Related decision + callers.
```

This shape lets an agent extract structure with grep — e.g. `grep -A1 "## Why this design" docs/**/*.md`.

## Linking

Cross-references inside `docs/` use relative paths. Source-code references reach back to the repo root with `../../` (e.g. `../../backend/main.py#L120-L180`). Use line ranges, not vague descriptions, so a follow-up edit elsewhere is observable through link rot.
