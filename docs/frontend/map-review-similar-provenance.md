# Review · Similar · Provenance Panels

**Paths:**
- [frontend/src/components/map/ReviewPanel.tsx](../../frontend/src/components/map/ReviewPanel.tsx) — bulk review/triage UI
- [frontend/src/components/map/SimilarPanel.tsx](../../frontend/src/components/map/SimilarPanel.tsx) — embedding-NN list
- [frontend/src/components/map/ProvenancePanel.tsx](../../frontend/src/components/map/ProvenancePanel.tsx) — chip/detector/model lineage for a single detection

## Purpose

Three smaller right-rail panels complementing [SelectionPanel.tsx](map-selection-panel.md).

## ReviewPanel

- Source: `GET /api/detections/queue` (high-priority review queue)
- Bulk-update review status: keep / discard / flag for follow-up
- Triggers `PATCH /api/detections/{id}/review`
- Queue loads carry a monotonic sequence token keyed to the status tab — a quick PENDING→ACCEPTED switch can no longer let the slower (stale) tab's response win the rows/header count
- `onJump(id, lat, lon)` passes the row's lat/lon up so [SelectionPanel](map-selection-panel.md) → GaiaMap `jumpToDetection` can pan even when the row is outside the viewport GeoJSON (the queue is global)

## SimilarPanel

- Source: `GET /api/detections/{id}/similar` (embedding NN)
- Side-by-side thumbnail grid; click pivots map focus to the similar detection via `onSelect(id, lat, lon)` (same global-jump path as ReviewPanel)
- Distance shown as cosine similarity
- Results are cleared at load start and guarded by a sequence token — switching anchors quickly can no longer show the old anchor's grid under the new anchor's header, and a late old response never wins

## ProvenancePanel

- Source: detection record's `metadata` field (no extra API call)
- Shows: chip URL, chip index, detector layer (sam3, dota_obb, etc.), model version, taxonomy version
- "Open chip" reveals the source chip with the detection mask overlaid
- **Wired as the SelectionPanel "Prov" tab** (`rightTab === 'provenance'`) — `import ProvenancePanel from './ProvenancePanel'` in [SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx), rendered via `{rightTab === 'provenance' && <ProvenancePanel selectedDetection={selectedDetection} />}`. GaiaMap owns the `rightTab` state and the tour `tab-provenance → setRightTab('provenance')` mapping; the tour step lives in [tourSteps.ts](../../frontend/src/components/tour/tourSteps.ts). (Was orphan-mounted before this wiring.)

### Detector ensemble panel (Task 1.3)

A `Cpu`-iconed Panel sits between "Model + sensor" and "Taxonomy" and
surfaces the multi-detector story the right-rail chip teases:

| Row | Value source |
|---|---|
| Primary detector | `detectionProvenance(props).primary` — display name of `source_layer`. |
| Fusion partners  | `detectionProvenance(props).partners.join(', ')` — empty renders as `— (single-detector)`. |
| WBF members      | `metadata.wbf_member_count` (or top-level `wbf_member_count`); falls back to 1. |
| Mask IoU (fusion) | `metadata.fusion_mask_iou` if present, otherwise `—`. Field is reserved for Task 2.8's WBF wiring; the row already exists so the schema doesn't need a second edit. |

A small italic caption under the rows reminds analysts that single-detector
calls are advisory until a second detector or analyst confirms. Helper
lives in [_helpers.ts](../../frontend/src/components/map/_helpers.ts) as
`detectionProvenance`; see [map-selection-panel.md](map-selection-panel.md)
for the matching header chip.

## Cross-references

- [map-selection-panel.md](map-selection-panel.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
