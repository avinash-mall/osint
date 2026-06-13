# Agent Context Map

**Path:** [docs/agent-context-map.md](agent-context-map.md)
**Lines:** ~73
**Depends on:** [INDEX.txt](INDEX.txt), [conventions/documentation-workflow.md](conventions/documentation-workflow.md)

## Purpose

Tell agents which small doc cluster to read for a task area, so they do not load
the whole tree to find the relevant constraints.

## Why this Design

`INDEX.txt` is exhaustive, but agents still need a routing table. This map keeps
the high-frequency "read first" choices out of root pointer files and avoids
copying the same guidance into multiple tool-specific entry points.

## Key Symbols

- **Backend/API/router work** — read [backend/api-routes-reference.md](backend/api-routes-reference.md), the matching `backend-routers/*.md`, [backend/main-app-entrypoint.md](backend/main-app-entrypoint.md), and [conventions/adding-a-new-router.md](conventions/adding-a-new-router.md).
- **Worker/Celery work** — read [backend/worker-package-facade.md](backend/worker-package-facade.md), [backend/worker-legacy-monolith.md](backend/worker-legacy-monolith.md), [operations/celery-queues-and-tasks.md](operations/celery-queues-and-tasks.md), and [conventions/adding-a-new-celery-task.md](conventions/adding-a-new-celery-task.md).
- **Inference/image model work** — read [inference/service-overview.md](inference/service-overview.md), [inference/main-app-entrypoint.md](inference/main-app-entrypoint.md), [inference/profile-pool-lifecycle.md](inference/profile-pool-lifecycle.md), [inference/fusion-and-nms.md](inference/fusion-and-nms.md), and [conventions/adding-a-new-detection-model.md](conventions/adding-a-new-detection-model.md).
- **FMV work** — read [architecture/data-flow-fmv.md](architecture/data-flow-fmv.md), [operations/fmv-ingest-pipeline.md](operations/fmv-ingest-pipeline.md), [backend/fmv-track-consolidation.md](backend/fmv-track-consolidation.md), [inference/sam3-pcs-multiplex-video.md](inference/sam3-pcs-multiplex-video.md), and [inference/yoloe-tracker.md](inference/yoloe-tracker.md).
- **Imagery/change-detection work** — read [architecture/data-flow-imagery.md](architecture/data-flow-imagery.md), [operations/imagery-ingest-pipeline.md](operations/imagery-ingest-pipeline.md), [backend/change-detection-raster.md](backend/change-detection-raster.md), and [operations/change-detection-runbook.md](operations/change-detection-runbook.md).
- **Ontology/prompt work** — read [backend/ontology-system.md](backend/ontology-system.md), [operations/ontology-edit-workflow.md](operations/ontology-edit-workflow.md), [operations/unknown-label-triage.md](operations/unknown-label-triage.md), [decisions/why-open-vocabulary.md](decisions/why-open-vocabulary.md), and [conventions/adding-a-new-ontology-object.md](conventions/adding-a-new-ontology-object.md).
- **Frontend map workspace work** — read [frontend/workspace-geoint-gaiamap.md](frontend/workspace-geoint-gaiamap.md), the specific `frontend/map-*.md` module doc, [frontend/product-tour.md](frontend/product-tour.md), and relevant frontend decision docs.
- **Admin UI work** — read [frontend/workspace-admin.md](frontend/workspace-admin.md), the specific `frontend/admin-*.md` module doc, and [conventions/adding-a-new-admin-tab.md](conventions/adding-a-new-admin-tab.md).
- **Deployment/GPU work** — read [deployment/docker-compose-services.md](deployment/docker-compose-services.md), [deployment/gpu-profile-detection.md](deployment/gpu-profile-detection.md), [deployment/environment-variables-reference.md](deployment/environment-variables-reference.md), [scripts/configure-host-gpu.md](scripts/configure-host-gpu.md), and [deployment/offline-airgap-deployment.md](deployment/offline-airgap-deployment.md).
- **Graph/analytics work** — read [architecture/link-graph-redesign.md](architecture/link-graph-redesign.md), [backend/graph-schema.md](backend/graph-schema.md), [backend/graph-writes.md](backend/graph-writes.md), and the matching analytics/router docs.
- **Scripts/benchmarks work** — read the matching [scripts/](scripts/) or [benchmarks/](benchmarks/) module doc and [testing/benchmark-harness.md](testing/benchmark-harness.md) when changing eval tooling.
- **Docs-only cleanup** — read [conventions/documentation-workflow.md](conventions/documentation-workflow.md), run [scripts/docs_audit.py](../scripts/docs_audit.py), and update [INDEX.txt](INDEX.txt).

## Inputs / Outputs

Input: a broad work area. Output: the smallest useful doc set to read before
editing.

## Failure Modes

- If a module is absent from this map, search [INDEX.txt](INDEX.txt) by path and
  tags.
- If a doc listed here is stale, update it in the same change that updates the
  affected source/config.

## Cross-References

- [agent-entry.md](agent-entry.md)
- [INDEX.txt](INDEX.txt)
- [conventions/documentation-workflow.md](conventions/documentation-workflow.md)
