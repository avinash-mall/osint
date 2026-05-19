# Naming & Paths

## Routes

- **Public REST:** prefix `/api/` for everything except `/auth/*` (legacy) and `/ws`. Admin-only routes use `/api/admin/`. The session middleware in [backend/main.py#L84](../../backend/main.py#L84) gates mutating verbs.
- **Health:** `/api/health` (public), `/api/alerts` (public).
- **WS:** the only WebSocket endpoint is `/ws`.

## Files

| Layer | Convention | Examples |
|---|---|---|
| Backend module | `snake_case.py` | `detection_policy.py`, `imagery_metadata.py` |
| Router | `routers/<name>.py` | `routers/ontology.py`, `routers/fmv.py` |
| Pydantic shape | `CamelCase` class in `schemas.py` | `ObjectDetailsBody`, `IngestUrlRequest` |
| Celery task name | `worker.<verb_noun>` literal `name=` arg | `worker.process_satellite_imagery` |
| Frontend component | `PascalCase.tsx`, one component per file | `FmvPlayer.tsx`, `OntologyAdmin.tsx` |
| Frontend hook | `useXxx.ts` | `useAuth.ts`, `useEventStream.ts` |
| Frontend util | `camelCase.ts/.tsx` | `branchIcons.tsx`, `detectionTaxonomy.ts` |
| Docs | `kebab-case.md` | `why-yoloe-replaced-amg.md`, `ontology-admin-ui.md` |

## Volume paths (inside container)

| Mount | Purpose |
|---|---|
| `/data/imagery/` | COGs, chips, uploads |
| `/data/fmv/` | clips + HLS segments |
| `/data/datasets/` | training datasets |
| `/data/dem/dem.tif` | DEM for analytics |
| `/data/routing/graph.pkl` | osmnx graph |
| `/data/calibration/` | calibration JSON |

Override via env: `IMAGERY_PATH`, `FMV_PATH`, `DATASET_PATH`, `DEM_PATH`, `ROUTING_GRAPH_PATH`. See [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Cross-references

- [coding-style.md](coding-style.md)
- [deployment/volume-mounts-and-paths.md](../deployment/volume-mounts-and-paths.md)
- [adding-a-new-router.md](adding-a-new-router.md)
