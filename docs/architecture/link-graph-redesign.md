# Link Graph Redesign — Defence-Analyst Reasoning Surface

**Status:** Phases 1–5 shipped (28 + 13 = 41 commits ahead of origin/main at time of Phase 5 close). Every deferred item from the original roll-up is now closed; see the "Phase 5 — deferred-items roll-up" section below.
**Plan file:** [~/.claude/plans/the-useful-framing-is-replicated-crane.md](file:///home/avinash/.claude/plans/the-useful-framing-is-replicated-crane.md) (canonical source; this doc is the in-repo mirror).
**Primary surfaces:** [backend/routers/graph.py](../../backend/routers/graph.py), [frontend/src/components/GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx).

## Context

The current Link Graph workspace ([frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)) is a generic force-directed Neo4j viewer. A read of the codebase shows the graph is mostly hollow today:

- **Nodes actually written by code:** `Detection`, `SatellitePass`, `Target`, `OntologyUpdate`, `OntologyCandidate`.
- **Edges actually written:** `CONTAINS_DETECTION` ([backend/worker_legacy.py#L2588](../../backend/worker_legacy.py#L2588)), `DETECTED_AS` ([backend/main.py#L1974](../../backend/main.py#L1974)), `PROPOSES`, `SUPPORTED_BY`, `CANDIDATE_RELATED_TO` ([backend/main.py#L417-L498](../../backend/main.py#L417-L498)).
- **Referenced in queries but never populated:** `Asset`, `Observation`, `Base`, `LaunchPoint`, edges `OBSERVED_AT`, `OBSERVED_BY`, `WITHIN`, `LAUNCHED_FROM`.
- **FMV never reaches the graph** — `fmv_clips/frames/detections` ([backend/platform_schema.py#L138-L176](../../backend/platform_schema.py#L138-L176)) are PostGIS-only.
- **Provenance-rich PostGIS tables exist but are not graph-connected:** `documents` (with `extracted_entities` JSONB), `reports`, `feed_events`, `observations`, `transcripts`, `timeline_events`, `aois`, `ontology_unknown_labels`.

This makes the Link Graph "visually impressive but analytically weak." The redesign turns it into a **reasoning surface** that answers six concrete defence-analyst questions:

1. Why is this detection important?
2. Have we seen this before?
3. What else is associated with this site?
4. Is this an ontology problem or an intelligence problem?
5. What is the chain of evidence (Target → Detection → SatellitePass/FMVClip → file/time/model/confidence)?
6. Transitive Cypher queries: supply chains, command structure, node density.

The framing rule: **the ontology helps the analyst understand "what this thing is"; the link graph helps them understand "why this thing matters."**

---

## Target end-state

### Three rendering modes inside ONE Link Graph workspace

Sub-tabs in the panel header of [GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx) (next to the Force/Hier/Geo toggle at #L375-L379). Sub-tabs (not sibling workspaces) preserve shared selection state (current node, time range, AOI scope, class lens).

- **Investigation** — default. Operational + Evidence nodes with time scrubber, class lens, predicate chips, path queries, site-composition rollup.
- **Evidence** — single-Target/Detection column DAG: entity → SatellitePass / FMVClip / Document / Report / FeedEvent, with model version, confidence, tier, and review status.
- **Ontology** — branches/objects/prompts as a graph, `UnknownLabel` nodes orbiting their suggested branch with `LABEL_OF` edges out to supporting Detections.

### Node label inventory (DB ownership)

| Class | Label | DB | Notes |
|---|---|---|---|
| Operational | `Target` | Neo4j | Already exists. |
| Operational | `Base`, `LaunchPoint`, `Facility` | Neo4j (id) + PostGIS (geometry) | MERGEd from `aois` tagged by `aoi_kind` in metadata. Mirrors the `SatellitePass` pattern at [worker_legacy.py#L3491](../../backend/worker_legacy.py#L3491). |
| Operational | `Vessel`, `Aircraft`, `Vehicle` | Neo4j | Identity-only; spatial state derived from `Observation`/`Detection`. Use secondary label `:Asset:Vessel` so generic queries hit them. |
| Operational | `Unit` | Neo4j | Command/organisation structure; no geometry. |
| Evidence | `Detection`, `SatellitePass` | PostGIS canonical + Neo4j mirror | Pattern already in place. |
| Evidence | `FMVClip`, `FMVDetection` | PostGIS canonical + Neo4j mirror | `FMVDetection` projected at the consolidated **track** level (one node per `track_uid`), not per-frame. |
| Evidence | `Document`, `Report`, `FeedEvent` | PostGIS canonical + Neo4j stub | Stub = `{postgis_id, title, kind, timestamp}`. Body never duplicates. |
| Evidence | `Observation` | Neo4j | Projected from `observations` / `feed_events` rows that carry `entity_id`. |
| Ontology | `OntologyBranch`, `OntologyObject` | PostGIS canonical + Neo4j mirror | Materialised by projector + version-bump hook in [routers/ontology.py](../../backend/routers/ontology.py). |
| Ontology | `UnknownLabel` | PostGIS canonical + Neo4j mirror | Mirror lets analyst see the unknown-label-orbit graph. |
| Ontology | `OntologyUpdate`, `OntologyCandidate`, `PromptProfile` | Neo4j (exists) / mirror | `PromptProfile` mirrored only to render "proposal came from profile X" edges. |

**Principle:** if a node is **traversed** in a graph query → project to Neo4j. If it is only **fetched on click** → stays in PostGIS and is stitched at the route level via `/api/graph/evidence/{id}`. Stub nodes hold identity + minimal headline properties; full rows never duplicate. See [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md) — Phase 2 adds a `why-postgis-to-neo4j-projectors.md` companion to clarify the one-way projection.

### Edge predicate inventory (writer / properties)

| Predicate | Writer | Notes |
|---|---|---|
| `CONTAINS_DETECTION` | satellite worker | Exists. SatellitePass → Detection. |
| `DETECTED_AS` | candidate-approval endpoint | Exists. Target → Detection. `status, reviewed_by, reviewed_at`. |
| `CANDIDATE_DETECTED_AS` | **new — persisted on candidate creation** | Currently synthesised in-memory at [routers/graph.py#L73](../../backend/routers/graph.py#L73). Persist as real edge with `score, reason, candidate_id, status='pending'`. |
| `OBSERVED_IN` | new approval path + FMV projector | Asset/Target → FMVClip / SatellitePass. `first_seen_at, last_seen_at`. |
| `OBSERVED_AT` | observations projector | Asset → Observation. `latitude, longitude, timestamp`. |
| `NEAR` | beat `worker.tick_near_builder` | Detection/Observation/Asset → Base/LaunchPoint/Facility. `distance_m, computed_at`. |
| `PART_OF` | analyst CRUD | Unit → Unit, Asset → Unit. |
| `OPERATES_FROM` | analyst CRUD + LLM proposal | Asset → Base. `confidence, source`. |
| `REPEATED_AT` | beat `worker.tick_repeat_detector` | Detection → Base/LaunchPoint when ≥N same-class detections within R metres in T days. `count, window_days, radius_m`. |
| `SAME_AS` | analyst approval only | Target ↔ Target, Asset ↔ Asset. `merged_by, merged_at`. |
| `POSSIBLY_SAME_AS` | beat `worker.tick_entity_resimilarity` (DINOv3 cosine) + LLM + analyst | `score, source ∈ {embedding, llm, analyst}, status`. |
| `SUPPORTED_BY` | LLM ontology pipeline (exists) + Document projector | OntologyCandidate → Detection / Document. |
| `CONTRADICTED_BY` | analyst action from Evidence mode | OntologyCandidate/Target → Detection. |
| `PROPOSES` | LLM ontology pipeline (exists) | OntologyUpdate → OntologyCandidate. |
| `CANDIDATE_RELATED_TO` | LLM (exists) | OntologyCandidate ↔ OntologyCandidate. |
| `LABEL_OF` | detection-class projector (Phase 3) | Detection → OntologyObject. Powers "show me everything classed as TEL" without text-matching. |
| `COLOCATED_WITH` | beat `worker.tick_colocation_builder` (Phase 6) | Detection ↔ Detection proximity edge from a vendored city2graph graph. `distance_m, method, computed_at`. See [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md). |
| `GNN_SUGGESTED_LINK` | beat `worker.tick_gnn_link_prediction` (Phase 6) | Operational entity ↔ entity advisory GNN link prediction (GraphSAGE). `score, model='graphsage'`. Never auto-promoted — analyst review only. See [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md). |
| `SUGGESTED_BRANCH` | UnknownLabel projector | UnknownLabel → OntologyBranch (from `ontology_unknown_labels.suggested_branch_id`). |
| `MENTIONS` | Document projector | Document → Target/Asset (resolved from `documents.extracted_entities` JSONB at [routers/ingest.py](../../backend/routers/ingest.py)). |
| `ABOUT` | report-link projector | Report → Target (from `reports.target_id`). |

### Visibility rule per mode

- **Investigation:** Operational always visible; Evidence only inside 2-hop neighborhood of selection; Ontology hidden unless the analyst toggles the class-lens chip or an Ontology node is on a returned path.
- **Evidence:** every node 2-hop from the focus, columnised by kind.
- **Ontology:** branches/objects/prompts + UnknownLabel orbit + co-occurrence; operational entities hidden.

This makes the "Ontology nodes only appear when they explain evidence/classification" rule enforceable.

---

## Phased rollout

Each phase is independently shippable and analyst-useful.

### Phase 1 — Investigation mode shell

**Goal:** turn the Link Graph into a working POL + path-discovery surface using entities that already exist, plus a minimal `Base/LaunchPoint` projection from AOIs.

**Backend deliverables**
- `backend/graph_schema.py` (new) — registers Neo4j constraints + indexes (see [Cross-cutting backend](#cross-cutting-backend-specifications)). Called from FastAPI lifespan.
- [backend/routers/graph.py](../../backend/routers/graph.py) extensions:
  - `GET /api/graph/investigation` — query params `mode_filter, class_lens, time_start, time_end, aoi_id, seed_node_id`. Replaces `/api/graph` for the investigation panel; old route kept for back-compat.
  - `POST /api/graph/path` — `{from_id, to_id, max_depth=4}`. Cypher `allShortestPaths`.
  - `GET /api/graph/site-composition/{base_id}` — workflow 3 rollup grouped by class (uses live ST_DWithin against PostGIS detections + Neo4j relations).
  - `POST /api/graph/candidate-edges/{candidate_id}/promote` — promotes a `CANDIDATE_DETECTED_AS` to `DETECTED_AS`.
- [backend/main.py](../../backend/main.py) — register new router(s); patch candidate-link creation so `CANDIDATE_DETECTED_AS` is persisted on row insert (currently only synthesised at [routers/graph.py#L73](../../backend/routers/graph.py#L73)).
- [backend/routers/ontology.py](../../backend/routers/ontology.py) (or wherever AOIs are created) — on AOI create with `aoi_kind ∈ {base, launchpoint, facility}` in metadata, MERGE the corresponding Neo4j node tied to the AOI by `postgis_id`.
- `backend/scripts/backfill_base_launchpoint_from_aois.py` (new) — idempotent MERGE.

**Frontend deliverables**
- [GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx):
  - Mode tab strip (Investigation / Ontology / Evidence) — Ontology + Evidence tabs are stubs in Phase 1.
  - Time scrubber — **reuse the `timeRange` + histogram pattern from [GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx)** (`timelineOpen`, `timeRange`, density histogram). Lift into a shared `TimeScrubber.tsx`.
  - Class-lens chip row — mirrors the `PredicateChipBar` pattern at [GraphExplorer.tsx#L113-L140](../../frontend/src/components/GraphExplorer.tsx#L113-L140).
  - Path-query mode: right-click → "Find path to..." prompts for second node, calls `/api/graph/path`.
  - "Roll up to site" action on Base/LaunchPoint nodes calls `/api/graph/site-composition/{id}` and groups results in the right panel.
  - Summarize-and-expand: cap default fetch at ~80 Operational nodes + their 1-hop neighborhood, ≤150 total. Existing per-node expansion at [GraphExplorer.tsx#L292-L300](../../frontend/src/components/GraphExplorer.tsx#L292-L300) is the drill-in.
  - Cluster collapse for ≥12 same-class neighbors of a single node (reuses groupedNodes pattern at [GraphExplorer.tsx#L221-L232](../../frontend/src/components/GraphExplorer.tsx#L221-L232)).

**Unlocks:** workflow 1 (partial — `NEAR` answered via live ST_DWithin), workflow 6 (paths).
**Out of scope until Phase 2:** FMV/Document evidence chain, full evidence-tier UI.

### Phase 2 — Evidence mode

**Goal:** answer workflow 5 (chain of evidence) fully and workflow 3 partially (FMV at site visible).

**Backend deliverables**
- [backend/routers/graph.py](../../backend/routers/graph.py) — `GET /api/graph/evidence/{node_id}` returns Neo4j 2-hop neighborhood + parallel PostGIS pull of transcripts, FMV frames, and feed_event payloads, all keyed by `postgis_id`. Single response shape: `{nodes, links, evidence_records}`.
- New Celery tasks (preserving `worker.xxx` naming per CLAUDE.md hard rule 6):
  - `worker.project_fmv_to_graph` — wired to the existing `fmv_detections_complete` channel emitted by `worker.consolidate_fmv` at [worker_legacy.py#L3216](../../backend/worker_legacy.py#L3216). MERGE `FMVClip` + per-track `FMVDetection` nodes + `CONTAINS_DETECTION` edges.
  - `worker.project_documents_to_graph` — triggered when `documents.extracted_entities` is populated (after [routers/ingest.py](../../backend/routers/ingest.py) finishes extraction). MERGE `:Document` stub + `:MENTIONS` edges to Targets/Assets resolved from `extracted_entities[].label` by fuzzy name match (LLM-assist optional).
  - `worker.project_observations_to_graph` — bridges `observations` + `feed_events` rows with `entity_id` to `:Observation` nodes + `:OBSERVED_AT` edges. Periodic backfill + on-insert hook.
- `backend/scripts/backfill_evidence_from_postgis.py` (new) — walks existing `documents`, `observations`, `fmv_clips`, idempotent MERGE.

**Frontend deliverables**
- Evidence mode rendering in `GraphExplorer.tsx`: column-ordered DAG (entity on left, leaf evidence on right), columns for SatellitePass / FMVClip / Document / Report / FeedEvent. Evidence tier (`confirmed/candidate/discovery`, per [backend/detection-evidence.md](../backend/detection-evidence.md)) drives node colour. Clicking a leaf opens a provenance popover (file path, model version, confidence, review status) — body fetched lazily.
- Right-click action **"Evidence chain"** on any node in Investigation switches to Evidence mode with that node as focus.
- Right-click action **"Contradict"** on a Detection/OntologyCandidate writes `CONTRADICTED_BY`.

**Unlocks:** workflows 3 (partial), 5 (full).
**Out of scope until Phase 3:** ontology-graph view, unknown-label orbit, `LABEL_OF` edges.

### Phase 3 — Ontology mode

**Goal:** answer workflow 4 (ontology vs intelligence problem). Move unknown-label triage from a list to a graph.

**Backend deliverables**
- [backend/routers/graph.py](../../backend/routers/graph.py) — `GET /api/graph/ontology?include_unknown=true&since=...`.
- New tasks:
  - `worker.project_unknown_labels` — mirrors `ontology_unknown_labels` rows (written at [backend/ontology.py#L275](../../backend/ontology.py#L275)) to `:UnknownLabel`. Builds `:SUGGESTED_BRANCH` to the OntologyBranch in `suggested_branch_id`. Adds on-write hook in [ontology.py](../../backend/ontology.py).
  - `worker.project_ontology_to_graph` — on `ontology_bump_version` ([routers/ontology.py](../../backend/routers/ontology.py)) MERGE `OntologyBranch` + `OntologyObject` mirrors with `HAS_OBJECT` parent edge.
  - `worker.project_label_of_edges` — builds `:LABEL_OF` from `Detection.class` via the ontology normalizer ([ontology.py](../../backend/ontology.py) `normalize()`). Runs incrementally per new Detection.

**Frontend deliverables**
- Ontology mode in `GraphExplorer.tsx`: branch/object tree as graph; `UnknownLabel` nodes orbit their suggested branch with `LABEL_OF` edges out to recent supporting Detections (so the analyst sees "where this label came from" at a glance).
- Co-occurrence chips on OntologyObject nodes — replaces the seeded synthetic bars at [GraphExplorer.tsx#L255-L258](../../frontend/src/components/GraphExplorer.tsx#L255-L258).
- **Reuse OntologyAdmin's unknown-label assignment form** ([OntologyAdmin.tsx#L792-L1042](../../frontend/src/components/OntologyAdmin.tsx#L792-L1042)) as a popover when an UnknownLabel node is clicked. The existing list view at [OntologyAdmin.tsx#L1402-L1487](../../frontend/src/components/OntologyAdmin.tsx#L1402-L1487) stays as "bulk view."

**Unlocks:** workflow 4.

### Phase 4 — Operational entities + NEAR + REPEATED_AT + SAME_AS

**Goal:** answer workflows 2 ("seen before") and 3 (full — vehicles/vessels at site) and enable arbitrary Cypher supply/command queries (workflow 6).

**Backend deliverables**
- `backend/routers/operational_entities.py` (new) — CRUD for Vessel/Aircraft/Vehicle/Facility/Unit. Routes:
  - `POST/GET/PATCH/DELETE /api/operational-entities/{kind}`
  - `POST /api/operational-entities/{kind}/{id}/attach-observation` writes `OBSERVED_AT`.
  - `POST /api/operational-entities/{kind}/{id}/operates-from/{base_id}` writes `OPERATES_FROM`.
- **LLM-proposed candidates** (chosen approach): extend [platform_schema.py#L376](../../backend/platform_schema.py#L376) (`detection_target_candidates`) into `entity_candidates` with `entity_kind` column; the existing approval/reject pattern at [main.py#L1937-L1993](../../backend/main.py#L1937-L1993) is the template. A new task `worker.tick_propose_entities` runs the LLM proposer.
- Beat tasks (registered in [worker_legacy.py#L245-L254](../../backend/worker_legacy.py#L245-L254) beat schedule):
  - `worker.tick_near_builder` (60 min). Per-class radius defaults: `Base` 5 km, `LaunchPoint` 2 km, `Facility` 1 km. Incremental (only new Detections since `computed_at`).
  - `worker.tick_repeat_detector` (daily). Thresholds per class from an admin-editable config.
  - `worker.tick_entity_resimilarity` (weekly). DINOv3 cosine over last 30 days within class + AOI; emits `POSSIBLY_SAME_AS` candidates.

**Frontend deliverables**
- New admin tab in [AdminScreen](../../frontend/src/components/AdminScreen.tsx) for operational-entity CRUD (template: [OntologyAdmin.tsx](../../frontend/src/components/OntologyAdmin.tsx)).
- Entity-candidate review subpanel in Investigation mode — mirrors the candidate-link approve/reject UI in [SelectionPanel.tsx#L517-L542](../../frontend/src/components/map/SelectionPanel.tsx#L517-L542).
- SAME_AS review screen: pending `POSSIBLY_SAME_AS` edges listed with the two entities side-by-side; approve rewrites to `SAME_AS`, optionally merges properties.
- Replace the live ST_DWithin fallback used in `site-composition` with traversal over precomputed `NEAR` edges.

**Unlocks:** workflows 2 (full), 3 (full), 6 (full transitive Cypher).

---

## Cross-cutting backend specifications

### Neo4j schema (registered in `backend/graph_schema.py`)

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Target)            REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Detection)         REQUIRE n.postgis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:SatellitePass)     REQUIRE n.postgis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:FMVClip)           REQUIRE n.postgis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:FMVDetection)      REQUIRE (n.clip_id, n.track_uid) IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Document)          REQUIRE n.postgis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Report)            REQUIRE n.postgis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:FeedEvent)         REQUIRE n.postgis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Observation)       REQUIRE n.postgis_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Asset)             REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Base)              REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:LaunchPoint)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Facility)          REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Unit)              REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:OntologyBranch)    REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:OntologyObject)    REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:OntologyCandidate) REQUIRE n.key IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:UnknownLabel)      REQUIRE n.label IS UNIQUE;
CREATE INDEX IF NOT EXISTS FOR (d:Detection) ON (d.class, d.created_at);
CREATE INDEX IF NOT EXISTS FOR ()-[r:NEAR]->() ON (r.distance_m);
```

### Routes added (all register in [backend/main.py](../../backend/main.py))

| Phase | Method | Path |
|---|---|---|
| 1 | GET | `/api/graph/investigation` |
| 1 | POST | `/api/graph/path` |
| 1 | GET | `/api/graph/site-composition/{base_id}` |
| 1 | POST | `/api/graph/candidate-edges/{candidate_id}/promote` |
| 2 | GET | `/api/graph/evidence/{node_id}` |
| 3 | GET | `/api/graph/ontology` |
| 4 | * | `/api/operational-entities/{kind}` (CRUD + actions) |
| 4 | POST | `/api/operational-entities/{kind}/{id}/same-as/{other_id}` |

### Celery tasks added (preserving `worker.xxx` naming — CLAUDE.md hard rule 6)

| Phase | Task | Cadence |
|---|---|---|
| 2 | `worker.project_fmv_to_graph` | event-driven (fmv_detections_complete) |
| 2 | `worker.project_documents_to_graph` | event-driven (document-ready) |
| 2 | `worker.project_observations_to_graph` | on-insert hook + nightly backfill |
| 3 | `worker.project_unknown_labels` | on-write hook in ontology.py |
| 3 | `worker.project_ontology_to_graph` | on `ontology_bump_version` |
| 3 | `worker.project_label_of_edges` | per new Detection |
| 4 | `worker.tick_near_builder` | beat, 60 min |
| 4 | `worker.tick_repeat_detector` | beat, daily |
| 4 | `worker.tick_entity_resimilarity` | beat, weekly |
| 4 | `worker.tick_propose_entities` | beat, daily |

### Documentation requirements (per [conventions/documentation-workflow.md](../conventions/documentation-workflow.md))

Each phase must:
1. Update or create module docs in [backend-routers/](../backend-routers/), [frontend/](../frontend/), [backend/](../backend/) per the six-section template.
2. Add a `decisions/<name>.md` for each architectural choice — at minimum:
   - `why-candidate-edges-persisted.md` (Phase 1).
   - `why-three-graph-modes.md` (Phase 1).
   - `why-postgis-to-neo4j-projectors.md` (Phase 2 — softens the "databases not synchronized" claim in [why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)).
   - `why-llm-proposed-entities.md` (Phase 4).
3. Add `conventions/adding-a-new-graph-projector.md` (Phase 2 — codifies the projector pattern).
4. Update [INDEX.txt](../INDEX.txt) for every new doc, sorted by path.
5. Refresh [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md) at end of each phase.

---

## Cross-cutting frontend specifications

- Single Link Graph workspace (existing `graph` entry in [Shell.tsx#L54-L65](../../frontend/src/components/Shell.tsx#L54-L65)) hosts three sub-tabs.
- Reuse, don't rebuild: `PredicateChipBar` ([GraphExplorer.tsx#L113-L140](../../frontend/src/components/GraphExplorer.tsx#L113-L140)), neighborhood expansion ([GraphExplorer.tsx#L277-L300](../../frontend/src/components/GraphExplorer.tsx#L277-L300)), time scrubber ([GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx) `timeRange` pattern), unknown-label assign form ([OntologyAdmin.tsx#L792-L1042](../../frontend/src/components/OntologyAdmin.tsx#L792-L1042)).
- `react-force-graph-2d` stays as the renderer. Node-count strategy: summarize-and-expand (≤150 default), per-node drill-in, cluster-collapse on ≥12 same-class neighbors, class lens + AOI scope chips. If Phase 4's NEAR materialisation pushes frames past 300 nodes regularly, evaluate `react-force-graph-3d` or sigma.js — defer until measured.

---

## Critical files

**Backend (modified or extended):**
- [backend/routers/graph.py](../../backend/routers/graph.py) — primary surface for all new routes.
- [backend/main.py](../../backend/main.py) — router registration; CANDIDATE_DETECTED_AS persistence patch.
- [backend/worker_legacy.py](../../backend/worker_legacy.py) — beat schedule + new projector tasks.
- [backend/platform_schema.py](../../backend/platform_schema.py) — `entity_candidates` table (Phase 4); `aoi_kind` metadata convention.
- [backend/routers/ontology.py](../../backend/routers/ontology.py) — on-write hooks for projectors.
- [backend/routers/ingest.py](../../backend/routers/ingest.py) — document-ready hook for projector.
- [backend/ontology.py](../../backend/ontology.py) — on-write hook for UnknownLabel mirror.
- [backend/candidate_linking.py](../../backend/candidate_linking.py) — caller of new CANDIDATE_DETECTED_AS persistence.

**Backend (new):**
- `backend/graph_schema.py`
- `backend/routers/operational_entities.py` (Phase 4)
- `backend/scripts/backfill_base_launchpoint_from_aois.py` (Phase 1)
- `backend/scripts/backfill_evidence_from_postgis.py` (Phase 2)

**Frontend (modified):**
- [frontend/src/components/GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx) — primary surface.
- [frontend/src/components/Shell.tsx](../../frontend/src/components/Shell.tsx) — no nav additions (sub-tabs go inside the workspace).
- [frontend/src/components/OntologyAdmin.tsx](../../frontend/src/components/OntologyAdmin.tsx) — extract triage form into a shared component (Phase 3).
- [frontend/src/components/map/SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx) — add "Open in Link Graph" actions.
- [frontend/src/components/AdminScreen.tsx](../../frontend/src/components/AdminScreen.tsx) — operational-entities tab (Phase 4).

**Frontend (new):**
- `frontend/src/components/graph/TimeScrubber.tsx` (Phase 1, extracted from GaiaMap).
- `frontend/src/components/graph/EvidenceColumnDAG.tsx` (Phase 2).
- `frontend/src/components/graph/OntologyOrbit.tsx` (Phase 3).
- `frontend/src/components/admin/OperationalEntitiesAdmin.tsx` (Phase 4).

---

## Verification per phase

End-to-end checks, all run in the docker-compose dev stack.

### Phase 1
- Create an AOI tagged `aoi_kind: base` → verify `MATCH (b:Base {postgis_id: <aoi_id>}) RETURN b` returns a node.
- Generate candidate links for a known Detection → verify `:CANDIDATE_DETECTED_AS` edge exists in Neo4j directly (not synthesised by the route).
- `GET /api/graph/investigation?aoi_id=<id>&time_start=...&time_end=...` → returns ≤150 nodes, includes the Base, includes recent Detections inside the AOI, includes their parent SatellitePass.
- `POST /api/graph/path` between two connected Targets → returns a path of length ≤4.
- `GET /api/graph/site-composition/<base_id>` → returns grouped buckets `{vessels, vehicles, aircraft, recent_detections, fmv_clips, reports}`. Empty groups acceptable in Phase 1.
- Frontend: open the workspace → mode tabs visible; Investigation default loads; time scrubber filters edges; path-query right-click works.
- Existing tests in [backend/tests/](../../backend/tests/) still green (`pytest backend/tests/`).

### Phase 2
- Upload an FMV clip → wait for `worker.consolidate_fmv` → verify `:FMVClip` + `:FMVDetection` nodes via `MATCH (c:FMVClip {postgis_id: <clip_id>})-[:CONTAINS_DETECTION]->(d) RETURN c, d`.
- Upload a document with named entities → verify `:Document` + `:MENTIONS` edges to existing Targets.
- `GET /api/graph/evidence/<target_id>` → returns 2-hop neighborhood + `evidence_records` array of PostGIS rows.
- Backfill script: `python backend/scripts/backfill_evidence_from_postgis.py --dry-run` → reports counts per label; live run is idempotent.
- Frontend: right-click "Evidence chain" on a Target → switches to Evidence mode showing column DAG with model_version/confidence/tier per leaf.

### Phase 3
- Add a new ontology branch via the admin UI → verify `:OntologyBranch` MERGE + `HAS_OBJECT` parents.
- Run inference on imagery with an out-of-vocabulary class → verify `ontology_unknown_labels` row written AND `:UnknownLabel` Neo4j node mirrored AND `:LABEL_OF` edge from the supporting Detection.
- `GET /api/graph/ontology?include_unknown=true` → returns branches + objects + UnknownLabels with `:SUGGESTED_BRANCH` edges.
- Frontend: Ontology tab renders unknown-label orbit; click an UnknownLabel → triage popover (reused from OntologyAdmin) opens; assign-to-existing closes the popover and removes the node.

### Phase 4
- Create a Vessel via admin CRUD → verify `:Vessel:Asset {id, callsign, hull, class}` exists.
- Run `worker.tick_propose_entities` manually → verify `entity_candidates` rows + frontend review subpanel renders proposals.
- After 60 min of fresh Detection writes: verify `worker.tick_near_builder` has populated `:NEAR` edges (`MATCH (d:Detection)-[r:NEAR]->(b:Base) WHERE r.computed_at > <recent> RETURN count(*)`).
- `GET /api/graph/site-composition/<base_id>` now serves results from `:NEAR` traversal (no ST_DWithin in the query plan).
- POSSIBLY_SAME_AS review screen: weekly job emits proposals; analyst approves → edge rewritten to `:SAME_AS`; properties merged with conflict prompts.

### Documentation gate (after every phase)
- New decision doc(s) for the phase's architectural choices.
- [INDEX.txt](../INDEX.txt) lines for every new/renamed doc, sorted by path, tags from the fixed vocabulary.
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md) reflects the current state (modes, endpoints, predicates).

---

## Phase 5 — deferred-items roll-up

Closed every item left open across Phases 1–4. The user picked the most-thorough scope on three open scope questions: full DINOv3 entity-embedding aggregation, full per-class threshold CRUD + admin tab, both unit and integration tests. LLM features reuse the existing OpenAI-compatible client in [backend/ai.py](../../backend/ai.py) (env: `OPENAI_API_BASE` / `OPENAI_API_KEY` / `OPENAI_MODEL`).

| Sub-phase | What |
|---|---|
| 5.A | Document projection gate — skip stub when `extracted_entities` empty. |
| 5.B | Per-class REPEATED_AT thresholds: table + CRUD router + admin tab + worker reader with env fallback. See [decisions/why-postgis-to-neo4j-projectors.md](../decisions/why-postgis-to-neo4j-projectors.md) sibling recipe [conventions/adding-a-new-admin-config-table.md](../conventions/adding-a-new-admin-config-table.md). |
| 5.C/D | `include_cooccurrence` on `/api/graph/ontology` + per-object chips in OntologyOrbit. |
| 5.E | Canvas cluster collapse for ≥12 same-class neighbours in GraphExplorer. |
| 5.F | `GET /api/operational-entities/pending-same-as` + reject endpoint. |
| 5.G | SAME_AS side-by-side review sub-panel in the operational-entities admin tab. |
| 5.H | PostGIS row property-merge endpoint + per-column resolution modal. |
| 5.I | LLM-driven entity proposer with heuristic fallback. See [decisions/why-llm-replaces-heuristic-proposer.md](../decisions/why-llm-replaces-heuristic-proposer.md). |
| 5.J | DINOv3-embedding cosine for POSSIBLY_SAME_AS: entity-level centroid + aggregator task + cosine branch. See [decisions/why-entity-embedding-aggregation.md](../decisions/why-entity-embedding-aggregation.md). |
| 5.K | POSSIBLY_SAME_AS time + AOI scoping for both branches. |
| 5.L | FMV + Reports buckets in `/api/graph/site-composition` (PostGIS spatial intersect for FMV; Neo4j-2-hop + PostGIS join for Reports). |
| 5.M | Unit + integration tests for all four tick tasks (`tick_near_builder`, `tick_repeat_detector`, `tick_entity_resimilarity`, `tick_propose_entities`, `tick_aggregate_entity_embeddings`). |
| 5.N | This documentation refresh. |

## Open questions for analyst sign-off before Phase 4

- **Document projection scope** — recommend projecting only documents whose `extracted_entities` is non-empty. Confirm.
- **REPEATED_AT thresholds** — per-class defaults (N detections, R metres, T days) must come from somewhere. Recommend an admin-editable config table modelled on `prompt_profiles`.
- **Time-range default in Investigation mode** — 7d / 30d / all-time. Recommend 30 days.
- **`POSSIBLY_SAME_AS` window** — DINOv3 cosine is O(N²) per class per window. Recommend last 30 days + same class + same AOI; confirm tightening is acceptable.

## Cross-references

- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md) — current state of the Link Graph workspace.
- [backend-routers/graph-router.md](../backend-routers/graph-router.md) — current graph router.
- [backend/candidate-linking.md](../backend/candidate-linking.md) — scorer that feeds candidate edges.
- [backend/ontology-system.md](../backend/ontology-system.md) — the ontology system the redesign integrates with.
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md) — pre-existing decision; will be paired with `why-postgis-to-neo4j-projectors.md` in Phase 2.
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md) — pre-existing decision; constrains the operational-entity sourcing approach in Phase 4.
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md) — existing pattern reused for `entity_candidates` in Phase 4.
