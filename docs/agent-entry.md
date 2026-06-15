# Sentinel Agent Entry

**Path:** [docs/agent-entry.md](agent-entry.md)
**Lines:** ~88
**Depends on:** [docs/INDEX.txt](INDEX.txt), [agent-context-map.md](agent-context-map.md), [conventions/documentation-workflow.md](conventions/documentation-workflow.md)

## Purpose

Give coding agents one compact, current project brief instead of repeating the
same long instructions in `AGENTS.md`, `CLAUDE.md`, and editor rule files.

## Project Shape

Sentinel is an open-source GEOINT exploitation platform for defence analysts.
It is a single Docker Compose stack:

- `backend/`: FastAPI + PostGIS + Neo4j + Redis pubsub.
- `backend/worker/`: Celery imagery, FMV, graph, bake, and maintenance tasks.
- `inference-sam3/`: SAM 3 image, SAM 3.1 video, DINOv3-SAT embeddings,
  TerraMind SAR synthesis, DOTA-OBB, MVRSD, and YOLOE FMV tracking.
- `frontend/`: React 19 + Vite 8 analyst workspaces.
- `assets/` and `/data/*`: baked/offline assets and runtime data.

Current inference truth: Prithvi heads, FAIR1M-OBB detector, RemoteCLIP verifier,
SAM3 AMG, and the Grounding-DINO / LAE-DINO open-vocab detector layer were
removed. Keep FAIR1M reference-dataset mentions; that corpus still feeds
reference-platform embedding workflows.

## Mandatory Workflow

Before changing files:

1. Read [INDEX.txt](INDEX.txt).
2. Read the module docs for every file you will touch.
3. Read related decisions in [decisions/](decisions/) and recipes in
   [conventions/](conventions/). Use [agent-context-map.md](agent-context-map.md)
   to pick the small relevant set.

Before declaring done:

1. Update the module doc for every modified source/config/script file.
2. Refresh `**Lines:** ~NNN` when the underlying file changed by more than 10%.
3. Add a decision doc for architectural choices or removals.
4. Update [INDEX.txt](INDEX.txt) for new/renamed/deleted docs.
5. Grep docs for affected module names and fix stale cross-references.

Full rules: [conventions/documentation-workflow.md](conventions/documentation-workflow.md).

## Hard Rules

- Do not write runtime data dirs on the dev host: `/data/*`, `bench/`,
  `assets/static/basemap/`, `inference-sam3/yolo*.pt`,
  `inference-sam3/yoloe-*.pt`, `inference-sam3/mobileclip2_b.ts`.
- Run `python scripts/configure_host.py` before changing GPU env. Do not
  hand-edit the generated `.env` block or copy it across hosts.
- Every new router registers in [backend/main.py](../backend/main.py). Routers
  own their prefixes; session middleware gates mutating verbs.
- Ontology is the canonical prompt source. Do not hard-code class lists.
- Open-vocabulary labels are first-class. Suppress noise only with confidence
  floors and policy overrides, never by deleting classes.
- Preserve explicit Celery task names such as `@celery_app.task(name="worker.xxx")`.
- No `--no-verify`, no force-push, no `git config` edits.
- Preserve air-gap behavior: no runtime internet calls or online-only links.
- Keep Product Tours current when moving frontend controls with `data-tour`.

## Fast Reads

- [INDEX.txt](INDEX.txt) — compressed docs catalog.
- [agent-context-map.md](agent-context-map.md) — which docs to read by work area.
- [architecture/system-overview.md](architecture/system-overview.md) — topology.
- [backend/api-routes-reference.md](backend/api-routes-reference.md) — API surface.
- [inference/service-overview.md](inference/service-overview.md) — model service.
- [deployment/docker-compose-services.md](deployment/docker-compose-services.md) — Compose services.

## Key Symbols

- [AGENTS.md](../AGENTS.md), [CLAUDE.md](../CLAUDE.md), [.cursor/rules](../.cursor/rules) — thin wrappers pointing here.
- [scripts/docs_audit.py](../scripts/docs_audit.py) — verifies docs/index/link/route drift.

## Inputs / Outputs

Input: any agent session. Output: a small, stable reading path and a current set
of constraints for safe code/doc changes.

## Failure Modes

- If this file and a module doc disagree, trust the code plus the latest decision
  doc, then update the stale doc.
- If worktree changes pre-exist, treat them as user-owned. Work with them; do not
  revert unrelated edits.

## Cross-References

- [conventions/documentation-workflow.md](conventions/documentation-workflow.md)
- [decisions/removed-prithvi-battle-damage.md](decisions/removed-prithvi-battle-damage.md)
- [decisions/removed-fair1m-and-remoteclip.md](decisions/removed-fair1m-and-remoteclip.md)
- [decisions/removed-sam3-amg.md](decisions/removed-sam3-amg.md)
