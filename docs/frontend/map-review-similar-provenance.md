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

## SimilarPanel

- Source: `GET /api/detections/{id}/similar` (embedding NN)
- Side-by-side thumbnail grid; click pivots map focus to the similar detection
- Distance shown as cosine similarity

## ProvenancePanel

- Source: detection record's `metadata` field (no extra API call)
- Shows: chip URL, chip index, detector layer (sam3, dota_obb, etc.), model version, taxonomy version
- "Open chip" reveals the source chip with the detection mask overlaid

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
