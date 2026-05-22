# API Routes Reference

Complete route table. Routers in [backend/routers/](../../backend/routers/) own their slices; `main.py` owns the bulk reads at the bottom.

## Auth · Health · Realtime

| Method | Path | Source |
|---|---|---|
| `POST` | `/api/auth/login` | [auth-router.md](../backend-routers/auth-router.md) |
| `POST` | `/api/auth/logout` | [auth-router.md](../backend-routers/auth-router.md) |
| `GET` | `/api/auth/me` | [auth-router.md](../backend-routers/auth-router.md) |
| `GET` `PUT` `POST` | `/api/admin/auth/{config,test,test-connection}` | [auth-router.md](../backend-routers/auth-router.md) |
| `GET` | `/api/health` | [health-router.md](../backend-routers/health-router.md) |
| `GET` | `/api/alerts` | [health-router.md](../backend-routers/health-router.md) |
| `WS` | `/ws` | [websocket-router.md](../backend-routers/websocket-router.md) |

## Graph & Tracks

| Method | Path | Source |
|---|---|---|
| `GET` | `/api/graph` | [graph-router.md](../backend-routers/graph-router.md) |
| `POST` | `/api/graph/neighborhood` | [graph-router.md](../backend-routers/graph-router.md) |
| `GET` | `/api/geotime/features` | [graph-router.md](../backend-routers/graph-router.md) |
| `GET` | `/api/tracks` · `/api/tracks/detections` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/tracks/detections/reprocess` · `pin` · `DELETE pin` | [backend/main.py](../../backend/main.py) |

## Imagery, FMV & Detections

| Method | Path | Source |
|---|---|---|
| `GET` | `/api/imagery` · `/api/imagery/{id}/tiles` · `bands` | [imagery-router.md](../backend-routers/imagery-router.md) |
| `POST` | `/api/imagery/change` | [imagery-router.md](../backend-routers/imagery-router.md) |
| `POST` | `/api/ingest` · `/upload` · `/url` | [ingest-router.md](../backend-routers/ingest-router.md) |
| `GET` | `/api/ingest/uploads` · `/jobs/{task_id}` | [ingest-router.md](../backend-routers/ingest-router.md) |
| `POST` `GET` | `/api/fmv/clips` (+ `/{id}` · `/klv` · `/detections`) | upload in [ingest-router.md](../backend-routers/ingest-router.md); reads in [backend/main.py](../../backend/main.py) |
| `GET` `PUT` `DELETE` | `/api/fmv/detections/{id}/*` | [fmv-router.md](../backend-routers/fmv-router.md) |
| `GET` | `/api/detections` · `/geojson` · `/classes` · `/queue` · `/prithvi-overlays` | [backend/main.py](../../backend/main.py) |
| `GET` `PUT` | `/api/detections/{id}/details` | [detections-router.md](../backend-routers/detections-router.md) |
| `POST` | `/api/detections/manual` · `/resolve` | [detections-router.md](../backend-routers/detections-router.md) and [backend/main.py](../../backend/main.py) |
| `DELETE` | `/api/detections/{id}` | [detections-router.md](../backend-routers/detections-router.md) |
| `PATCH` | `/api/detections/{id}/tag` · `/review` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/{id}/similar` · `/candidate-links` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/detection-target-candidates/{id}/approve` · `/reject` | [backend/main.py](../../backend/main.py) |

## Inference Control

| Method | Path | Source |
|---|---|---|
| `POST` | `/api/inference/load` · `/unload` | [inference-router.md](../backend-routers/inference-router.md) |
| `GET` | `/api/inference/health` · `/dashboard` · `/confidence-overrides` | [inference-router.md](../backend-routers/inference-router.md) |
| `PUT` | `/api/inference/confidence-overrides` | [inference-router.md](../backend-routers/inference-router.md) |

## Ontology · Versioning

| Method | Path | Source |
|---|---|---|
| `GET` `POST` `PATCH` `DELETE` | `/api/ontology` · `/branches` · `/objects` | [ontology-router.md](../backend-routers/ontology-router.md) |
| `GET` | `/api/ontology/version` · `/default-prompts` · `/version-history` | [ontology-router.md](../backend-routers/ontology-router.md) |
| `GET` `POST` | `/api/ontology/unknown-labels` · `/{label}/assign` | [ontology-router.md](../backend-routers/ontology-router.md) |
| `GET` `POST` `PUT` `DELETE` | `/api/ontology/prompt-profiles[/{id}/activate]` | [ontology-router.md](../backend-routers/ontology-router.md) |
| `GET` | `/api/ontology/updates` | [ontology-router.md](../backend-routers/ontology-router.md) |
| `POST` | `/api/ontology/update` | [backend/main.py](../../backend/main.py) (LLM-proposed bulk edits) |

## Analytics · Models · Training · Alerts · Feeds · AI

| Method | Path | Source |
|---|---|---|
| `POST` | `/api/analytics/change` · `viewshed` · `los` · `routes` · `pol` | [analytics-router.md](../backend-routers/analytics-router.md) |
| `GET` | `/api/analytics/capabilities` · `/jobs` | [analytics-router.md](../backend-routers/analytics-router.md) |
| `GET` `POST` | `/api/models` · `/datasets` · `/{id}/promote` · `/api/training/jobs` | [models-training-router.md](../backend-routers/models-training-router.md) |
| `GET` | `/api/feeds` · `/api/observations` · `/api/timeline/events` | [backend/main.py](../../backend/main.py) |
| `POST` `PUT` | `/api/feeds/connect` · `/{id}/status` · `/events` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/collection/tasks` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/ai/analyze` · `extract` · `link` · `propose-actions` | [ai-router.md](../backend-routers/ai-router.md) |
| `GET` `POST` | `/api/actions/proposals[/{id}/approve|/execute]` | [ai-router.md](../backend-routers/ai-router.md) |

## Cross-references

- [backend/main-app-entrypoint.md](main-app-entrypoint.md)
- [backend/pydantic-schemas.md](pydantic-schemas.md)
