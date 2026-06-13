# API Routes Appendix

**Path:** [docs/backend/api-routes-appendix.md](api-routes-appendix.md)
**Lines:** ~203
**Depends on:** backend FastAPI decorators

## Purpose

Generated compact appendix of FastAPI and WebSocket decorators found under `backend/`.

## Why this Design

[api-routes-reference.md](api-routes-reference.md) groups routes for humans; this appendix gives agents an exact path list for drift checks.

## Key Symbols

| Method | Path | Source |
|---|---|---|
| `GET` | `/api/actions/proposals` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `POST` | `/api/actions/proposals/{proposal_id}/approve` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `POST` | `/api/actions/proposals/{proposal_id}/execute` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `GET` | `/api/admin/auth/config` | [backend/routers/auth.py](../../backend/routers/auth.py) |
| `PUT` | `/api/admin/auth/config` | [backend/routers/auth.py](../../backend/routers/auth.py) |
| `POST` | `/api/admin/auth/test` | [backend/routers/auth.py](../../backend/routers/auth.py) |
| `POST` | `/api/admin/auth/test-connection` | [backend/routers/auth.py](../../backend/routers/auth.py) |
| `POST` | `/api/admin/reference/seed` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `GET` | `/api/admin/repeat-thresholds` | [backend/routers/admin_thresholds.py](../../backend/routers/admin_thresholds.py) |
| `POST` | `/api/admin/repeat-thresholds` | [backend/routers/admin_thresholds.py](../../backend/routers/admin_thresholds.py) |
| `DELETE` | `/api/admin/repeat-thresholds/{threshold_id}` | [backend/routers/admin_thresholds.py](../../backend/routers/admin_thresholds.py) |
| `PUT` | `/api/admin/repeat-thresholds/{threshold_id}/activate` | [backend/routers/admin_thresholds.py](../../backend/routers/admin_thresholds.py) |
| `POST` | `/api/ai/analyze` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `POST` | `/api/ai/brief-area` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `POST` | `/api/ai/extract` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `POST` | `/api/ai/link` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `POST` | `/api/ai/propose-actions` | [backend/routers/ai.py](../../backend/routers/ai.py) |
| `GET` | `/api/alerts` | [backend/routers/health.py](../../backend/routers/health.py) |
| `GET` | `/api/analytics/capabilities` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `POST` | `/api/analytics/change` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `GET` | `/api/analytics/elevation` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `POST` | `/api/analytics/isochrone` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `GET` | `/api/analytics/jobs` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `POST` | `/api/analytics/los` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `POST` | `/api/analytics/od-flows` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `POST` | `/api/analytics/pol` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `POST` | `/api/analytics/routes` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `POST` | `/api/analytics/viewshed` | [backend/routers/analytics.py](../../backend/routers/analytics.py) |
| `GET` | `/api/aois` | [backend/routers/aois.py](../../backend/routers/aois.py) |
| `POST` | `/api/aois` | [backend/routers/aois.py](../../backend/routers/aois.py) |
| `DELETE` | `/api/aois/{aoi_id}` | [backend/routers/aois.py](../../backend/routers/aois.py) |
| `GET` | `/api/aois/{aoi_id}` | [backend/routers/aois.py](../../backend/routers/aois.py) |
| `PATCH` | `/api/aois/{aoi_id}` | [backend/routers/aois.py](../../backend/routers/aois.py) |
| `POST` | `/api/auth/login` | [backend/routers/auth.py](../../backend/routers/auth.py) |
| `POST` | `/api/auth/logout` | [backend/routers/auth.py](../../backend/routers/auth.py) |
| `GET` | `/api/auth/me` | [backend/routers/auth.py](../../backend/routers/auth.py) |
| `GET` | `/api/basemap/countries` | [backend/routers/imagery.py](../../backend/routers/imagery.py) |
| `POST` | `/api/collection/tasks` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/detection-target-candidates/{candidate_id}/approve` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/detection-target-candidates/{candidate_id}/reject` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/classes` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/geojson-lite` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/detections/manual` | [backend/routers/detections.py](../../backend/routers/detections.py) |
| `GET` | `/api/detections/queue` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/detections/resolve` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/tile-version` | [backend/routers/detections.py](../../backend/routers/detections.py) |
| `DELETE` | `/api/detections/{detection_id}` | [backend/routers/detections.py](../../backend/routers/detections.py) |
| `GET` | `/api/detections/{detection_id}/candidate-links` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/detections/{detection_id}/candidate-links` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/{detection_id}/details` | [backend/routers/detections.py](../../backend/routers/detections.py) |
| `PUT` | `/api/detections/{detection_id}/details` | [backend/routers/detections.py](../../backend/routers/detections.py) |
| `GET` | `/api/detections/{detection_id}/enriched` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/{detection_id}/identification-candidates` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `POST` | `/api/detections/{detection_id}/identify` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `PATCH` | `/api/detections/{detection_id}/review` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/detections/{detection_id}/similar` | [backend/main.py](../../backend/main.py) |
| `PATCH` | `/api/detections/{detection_id}/tag` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/dossier` | [backend/routers/imagery.py](../../backend/routers/imagery.py) |
| `GET` | `/api/feeds` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/feeds/connect` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/feeds/{feed_id}/events` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/feeds/{feed_id}/events` | [backend/main.py](../../backend/main.py) |
| `PUT` | `/api/feeds/{feed_id}/status` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/fmv/clips` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/fmv/clips` | [backend/main.py](../../backend/main.py) |
| `DELETE` | `/api/fmv/clips/{clip_id}` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/fmv/clips/{clip_id}` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/fmv/clips/{clip_id}/detections` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/fmv/clips/{clip_id}/klv` | [backend/main.py](../../backend/main.py) |
| `DELETE` | `/api/fmv/detections/{detection_id}` | [backend/routers/fmv.py](../../backend/routers/fmv.py) |
| `GET` | `/api/fmv/detections/{detection_id}/details` | [backend/routers/fmv.py](../../backend/routers/fmv.py) |
| `PUT` | `/api/fmv/detections/{detection_id}/details` | [backend/routers/fmv.py](../../backend/routers/fmv.py) |
| `GET` | `/api/fmv/detections/{detection_id}/similar` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/geotime/features` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `POST` | `/api/graph/candidate-edges/{candidate_id}/promote` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/classes` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/colocation` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `POST` | `/api/graph/contradict` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/evidence/{node_id}` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/export/stix` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/gnn/status` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `POST` | `/api/graph/gnn/suggest-links` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/investigation` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/metrics` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `POST` | `/api/graph/neighborhood` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/ontology` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/passes` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `POST` | `/api/graph/path` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/graph/site-composition/{base_id}` | [backend/routers/graph.py](../../backend/routers/graph.py) |
| `GET` | `/api/health` | [backend/routers/health.py](../../backend/routers/health.py) |
| `POST` | `/api/identification-candidates/{candidate_id}/approve` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `POST` | `/api/identification-candidates/{candidate_id}/reject` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `GET` | `/api/imagery` | [backend/routers/imagery.py](../../backend/routers/imagery.py) |
| `POST` | `/api/imagery/change` | [backend/routers/imagery.py](../../backend/routers/imagery.py) |
| `DELETE` | `/api/imagery/{pass_id}` | [backend/routers/imagery.py](../../backend/routers/imagery.py) |
| `GET` | `/api/imagery/{pass_id}/bands` | [backend/routers/imagery.py](../../backend/routers/imagery.py) |
| `GET` | `/api/imagery/{pass_id}/tiles` | [backend/routers/imagery.py](../../backend/routers/imagery.py) |
| `GET` | `/api/inference/confidence-overrides` | [backend/routers/inference.py](../../backend/routers/inference.py) |
| `PUT` | `/api/inference/confidence-overrides` | [backend/routers/inference.py](../../backend/routers/inference.py) |
| `GET` | `/api/inference/dashboard` | [backend/routers/inference.py](../../backend/routers/inference.py) |
| `GET` | `/api/inference/health` | [backend/routers/inference.py](../../backend/routers/inference.py) |
| `POST` | `/api/inference/load` | [backend/routers/inference.py](../../backend/routers/inference.py) |
| `POST` | `/api/inference/unload` | [backend/routers/inference.py](../../backend/routers/inference.py) |
| `POST` | `/api/ingest` | [backend/routers/ingest.py](../../backend/routers/ingest.py) |
| `GET` | `/api/ingest/jobs/{task_id}` | [backend/routers/ingest.py](../../backend/routers/ingest.py) |
| `POST` | `/api/ingest/upload` | [backend/routers/ingest.py](../../backend/routers/ingest.py) |
| `GET` | `/api/ingest/uploads` | [backend/routers/ingest.py](../../backend/routers/ingest.py) |
| `POST` | `/api/ingest/url` | [backend/routers/ingest.py](../../backend/routers/ingest.py) |
| `GET` | `/api/models` | [backend/routers/models_training.py](../../backend/routers/models_training.py) |
| `GET` | `/api/models/datasets` | [backend/routers/models_training.py](../../backend/routers/models_training.py) |
| `POST` | `/api/models/datasets` | [backend/routers/models_training.py](../../backend/routers/models_training.py) |
| `POST` | `/api/models/{model_id}/promote` | [backend/routers/models_training.py](../../backend/routers/models_training.py) |
| `GET` | `/api/observations` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/ontology` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `POST` | `/api/ontology/branches` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `DELETE` | `/api/ontology/branches/{branch_id}` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `PATCH` | `/api/ontology/branches/{branch_id}` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `GET` | `/api/ontology/default-prompts` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `POST` | `/api/ontology/objects` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `DELETE` | `/api/ontology/objects/{object_id}` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `PATCH` | `/api/ontology/objects/{object_id}` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `GET` | `/api/ontology/prompt-profiles` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `POST` | `/api/ontology/prompt-profiles` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `DELETE` | `/api/ontology/prompt-profiles/{profile_id}` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `PUT` | `/api/ontology/prompt-profiles/{profile_id}/activate` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `GET` | `/api/ontology/unknown-labels` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `POST` | `/api/ontology/unknown-labels/{label}/assign` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `GET` | `/api/ontology/updates` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `GET` | `/api/ontology/version` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `GET` | `/api/ontology/version-history` | [backend/routers/ontology.py](../../backend/routers/ontology.py) |
| `GET` | `/api/operational-entities` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `GET` | `/api/operational-entities/pending-same-as` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities/pending-same-as/reject` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities/{a_id}/merge-into/{b_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `DELETE` | `/api/operational-entities/{entity_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `GET` | `/api/operational-entities/{entity_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `PATCH` | `/api/operational-entities/{entity_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities/{entity_id}/attach-observation` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities/{entity_id}/attach-track/{track_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities/{entity_id}/operates-from/{base_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities/{entity_id}/part-of/{unit_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entities/{entity_id}/same-as/{other_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `GET` | `/api/operational-entities/{entity_id}/tracks` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `DELETE` | `/api/operational-entities/{entity_id}/tracks/{track_id}` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `GET` | `/api/operational-entity-candidates` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entity-candidates/{candidate_id}/approve` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `POST` | `/api/operational-entity-candidates/{candidate_id}/reject` | [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py) |
| `GET` | `/api/reference-chips/{chip_id}/image` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `GET` | `/api/reference-platforms` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `GET` | `/api/reference-platforms/{platform_id}` | [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) |
| `POST` | `/api/reports/target-package/{detection_id}` | [backend/routers/reports.py](../../backend/routers/reports.py) |
| `GET` | `/api/satellites/anomalies` | [backend/routers/satellites.py](../../backend/routers/satellites.py) |
| `GET` | `/api/satellites/ground-track/{norad_id}` | [backend/routers/satellites.py](../../backend/routers/satellites.py) |
| `POST` | `/api/satellites/passes` | [backend/routers/satellites.py](../../backend/routers/satellites.py) |
| `GET` | `/api/satellites/tle` | [backend/routers/satellites.py](../../backend/routers/satellites.py) |
| `POST` | `/api/satellites/tle` | [backend/routers/satellites.py](../../backend/routers/satellites.py) |
| `GET` | `/api/sources` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/sources/{source_id}/events` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/system/deployment-mode` | [backend/routers/system.py](../../backend/routers/system.py) |
| `GET` | `/api/timeline/events` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/tracks` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/tracks/detections` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/tracks/detections/pin` | [backend/main.py](../../backend/main.py) |
| `POST` | `/api/tracks/detections/reprocess` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/tracks/detections/{track_uid}` | [backend/main.py](../../backend/main.py) |
| `DELETE` | `/api/tracks/detections/{track_uid}/pin` | [backend/main.py](../../backend/main.py) |
| `GET` | `/api/training/jobs` | [backend/routers/models_training.py](../../backend/routers/models_training.py) |
| `POST` | `/api/training/jobs` | [backend/routers/models_training.py](../../backend/routers/models_training.py) |
| `WS` | `/ws` | [backend/routers/ws.py](../../backend/routers/ws.py) |

## Inputs / Outputs

Input: route decorators in backend Python files. Output: this route table.

## Failure Modes

Dynamic routes whose path is not a string literal are skipped and should be documented manually.

## Cross-References

- [api-routes-reference.md](api-routes-reference.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
