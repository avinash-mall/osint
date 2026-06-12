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

Link Graph redesign added the bulk of these — see [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) for the phasing.

| Method | Path | Source |
|---|---|---|
| `GET` | `/api/graph` | [graph-router.md](../backend-routers/graph-router.md) — unbounded slice, `det_class`+`pass_id` scope |
| `GET` | `/api/graph/classes` | [graph-router.md](../backend-routers/graph-router.md) — detection classes + counts (dropdown) |
| `GET` | `/api/graph/passes` | [graph-router.md](../backend-routers/graph-router.md) — imagery scenes + counts (image dropdown) |
| `POST` | `/api/graph/neighborhood` | [graph-router.md](../backend-routers/graph-router.md) |
| `GET` | `/api/geotime/features` | [graph-router.md](../backend-routers/graph-router.md) |
| `GET` | `/api/graph/investigation` | [graph-router.md](../backend-routers/graph-router.md) — bounded operational + 1-hop |
| `POST` | `/api/graph/path` | [graph-router.md](../backend-routers/graph-router.md) — allShortestPaths |
| `GET` | `/api/graph/site-composition/{base_id}` | [graph-router.md](../backend-routers/graph-router.md) — workflow 3 rollup w/ FMV + Reports |
| `GET` | `/api/graph/evidence/{node_id}` | [graph-router.md](../backend-routers/graph-router.md) — workflow 5 chain |
| `GET` | `/api/graph/ontology` | [graph-router.md](../backend-routers/graph-router.md) — ontology mode + co-occurrence |
| `POST` | `/api/graph/contradict` | [graph-router.md](../backend-routers/graph-router.md) — dissent edge |
| `POST` | `/api/graph/candidate-edges/{candidate_id}/promote` | [graph-router.md](../backend-routers/graph-router.md) |
| `GET` | `/api/graph/colocation` | [graph-router.md](../backend-routers/graph-router.md) — Phase 6 proximity (co-location) graph preview |
| `GET` | `/api/graph/metrics` | [graph-router.md](../backend-routers/graph-router.md) — Phase 6 density/components/centrality (rustworkx) |
| `GET` | `/api/graph/gnn/status` | [graph-router.md](../backend-routers/graph-router.md) — GNN runnability (torch present?) |
| `POST` | `/api/graph/gnn/suggest-links` | [graph-router.md](../backend-routers/graph-router.md) — Phase 6 GraphSAGE link prediction; 503 without torch |
| `GET` `POST` `PATCH` `DELETE` | `/api/aois[/{id}]` | [aois-router.md](../backend-routers/aois-router.md) — projects Base/LaunchPoint/Facility |
| `GET` | `/api/tracks` · `/api/tracks/detections` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/tracks/detections/reprocess` · `pin` · `DELETE pin` | [backend/main.py](../../backend/main.py) |

## Operational entities · SAME_AS · Admin thresholds

| Method | Path | Source |
|---|---|---|
| `GET` `POST` `PATCH` `DELETE` | `/api/operational-entities[/{id}]` | [operational-entities-router.md](../backend-routers/operational-entities-router.md) |
| `POST` | `/api/operational-entities/{id}/attach-observation` · `operates-from/{base_id}` · `part-of/{unit_id}` · `same-as/{other_id}` | [operational-entities-router.md](../backend-routers/operational-entities-router.md) |
| `POST` `GET` `DELETE` | `/api/operational-entities/{id}/attach-track/{track_id}` · `/tracks` · `/tracks/{track_id}` | [operational-entities-router.md](../backend-routers/operational-entities-router.md) — Phase 5.J |
| `POST` | `/api/operational-entities/{a_id}/merge-into/{b_id}` | [operational-entities-router.md](../backend-routers/operational-entities-router.md) — Phase 5.H |
| `GET` `POST` | `/api/operational-entities/pending-same-as[/reject]` | [operational-entities-router.md](../backend-routers/operational-entities-router.md) — Phase 5.F |
| `GET` `POST` | `/api/operational-entity-candidates[/{id}/approve|/reject]` | [operational-entities-router.md](../backend-routers/operational-entities-router.md) — Phase 4.F |
| `GET` `POST` `PUT` `DELETE` | `/api/admin/repeat-thresholds[/{id}/activate]` | [admin-thresholds-router.md](../backend-routers/admin-thresholds-router.md) — Phase 5.B; admin session required |

## Imagery, FMV & Detections

| Method | Path | Source |
|---|---|---|
| `GET` | `/api/imagery` · `/api/imagery/{id}/tiles` · `bands` | [imagery-router.md](../backend-routers/imagery-router.md) |
| `DELETE` | `/api/imagery/{id}` | [imagery-router.md](../backend-routers/imagery-router.md) — admin; drops detections + COG file + Neo4j nodes |
| `POST` | `/api/imagery/change` | [imagery-router.md](../backend-routers/imagery-router.md) |
| `POST` | `/api/ingest` · `/upload` · `/url` | [ingest-router.md](../backend-routers/ingest-router.md) |
| `GET` | `/api/ingest/uploads` · `/jobs/{task_id}` | [ingest-router.md](../backend-routers/ingest-router.md) |
| `POST` `GET` `DELETE` | `/api/fmv/clips` (+ `/{id}` · `/klv` · `/detections`) | upload/reads/delete in [backend/main.py](../../backend/main.py); `DELETE` admin, drops detections+frames+files+Neo4j |
| `GET` `PUT` `DELETE` | `/api/fmv/detections/{id}/*` | [fmv-router.md](../backend-routers/fmv-router.md) |
| `GET` | `/api/detections` · `/geojson-lite` · `/classes` · `/queue` · `/prithvi-overlays` | [backend/main.py](../../backend/main.py) (`/classes` returns deterministic labels plus optional LLM advisory metadata) |
| `GET` `PUT` | `/api/detections/{id}/details` | [detections-router.md](../backend-routers/detections-router.md) |
| `POST` | `/api/detections/manual` · `/resolve` | [detections-router.md](../backend-routers/detections-router.md) and [backend/main.py](../../backend/main.py) |
| `DELETE` | `/api/detections/{id}` | [detections-router.md](../backend-routers/detections-router.md) |
| `PATCH` | `/api/detections/{id}/tag` · `/review` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/{id}/similar` · `/candidate-links` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/detection-target-candidates/{id}/approve` · `/reject` | [backend/main.py](../../backend/main.py) |

## Inference Control

| Method | Path | Source |
|---|---|---|
| `POST` | `/api/inference/load` · `/unload` | [inference-router.md](../backend-routers/inference-router.md) — admin session required |
| `GET` | `/api/inference/health` · `/dashboard` · `/confidence-overrides` | [inference-router.md](../backend-routers/inference-router.md) |
| `PUT` | `/api/inference/confidence-overrides` | [inference-router.md](../backend-routers/inference-router.md) |

## Ontology · Versioning

| Method | Path | Source |
|---|---|---|
| `GET` `POST` `PATCH` `DELETE` | `/api/ontology` · `/branches` · `/objects` | [ontology-router.md](../backend-routers/ontology-router.md) — branch/object mutations require admin |
| `GET` | `/api/ontology/version` · `/default-prompts` · `/version-history` | [ontology-router.md](../backend-routers/ontology-router.md) |
| `GET` `POST` | `/api/ontology/unknown-labels` · `/{label}/assign` | [ontology-router.md](../backend-routers/ontology-router.md) — assignment requires admin |
| `GET` `POST` `PUT` `DELETE` | `/api/ontology/prompt-profiles[/{id}/activate]` | [ontology-router.md](../backend-routers/ontology-router.md) — profile mutations require admin |
| `GET` | `/api/ontology/updates` | [ontology-router.md](../backend-routers/ontology-router.md) |

## Analytics · Models · Training · Alerts · Feeds · AI

| Method | Path | Source |
|---|---|---|
| `POST` | `/api/analytics/change` · `viewshed` · `los` · `routes` · `isochrone` · `od-flows` · `pol` | [analytics-router.md](../backend-routers/analytics-router.md) |
| `GET` | `/api/analytics/capabilities` · `/jobs` | [analytics-router.md](../backend-routers/analytics-router.md) |
| `GET` `POST` | `/api/satellites/tle` · `/passes` · `/ground-track/{id}` | [satellites-router.md](../backend-routers/satellites-router.md) — offline SGP4 overpass |
| `GET` `POST` | `/api/models` · `/datasets` · `/{id}/promote` · `/api/training/jobs` | [models-training-router.md](../backend-routers/models-training-router.md) — admin session required |
| `GET` | `/api/feeds` · `/api/observations` · `/api/timeline/events` | [backend/main.py](../../backend/main.py) |
| `POST` `PUT` | `/api/feeds/connect` · `/{id}/status` · `/events` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/collection/tasks` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/ai/analyze` · `extract` · `link` · `propose-actions` | [ai-router.md](../backend-routers/ai-router.md) |
| `GET` `POST` | `/api/actions/proposals[/{id}/approve|/execute]` | [ai-router.md](../backend-routers/ai-router.md) |

## Cross-references

- [backend/main-app-entrypoint.md](main-app-entrypoint.md)
- [backend/pydantic-schemas.md](pydantic-schemas.md)
