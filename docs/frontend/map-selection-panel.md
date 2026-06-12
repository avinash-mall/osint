# Selection Panel — Right Rail

**Path:** [frontend/src/components/map/SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx)
**Lines:** ~757
**Depends on:** [ObjectDetailsForm.tsx](../../frontend/src/components/ObjectDetailsForm.tsx), [IdentificationPanel.tsx](../../frontend/src/components/map/IdentificationPanel.tsx), [services/analytics.ts](../../frontend/src/services/analytics.ts), [_helpers.ts](../../frontend/src/components/map/_helpers.ts) `displayLabel` / `labelQuality` / `detectionProvenance`, backend `/api/detections`, `/api/analytics`, and `/api/reports`

## Purpose

Six-tab right rail. Most tabs key off the selected detection; the **Sat** tab
is detection-independent (overpass planning over any picked point).

## Tabs

| Tab | What it shows |
|---|---|
| **Details** | `ObjectDetailsForm` — threat, affiliation, notes, size estimation, original/canonical labels, provenance link. Identification subsection — see [identification-panel.md](identification-panel.md) — renders between Taxonomy and the cross-nav buttons, **remounted per detection** (`key={'ident-'+id}`) so a late candidates response can never sit under a new detection's header. The Geolocation WGS84 row formats hemispheres from the sign (`33.8600° S`, not `-33.8600° N`). The allegiance header chip tolerates both `friendly` (legacy rows) and `friend` (normalised writes). |
| **Analytics** | Buttons for viewshed/LOS/route/change-detection from this detection's location |
| **Sat** | Satellite overpass planning — an injected `satellitesSlot` node from GaiaMap (keeps this panel decoupled from the satellites service). Rendered by `{rightTab === 'satellites' && satellitesSlot}` in the content region. Offline SGP4, observer pick, ground track. See [map-satellites-panel.md](map-satellites-panel.md). |
| **Similar** | k-NN list of detections with similar embeddings (`GET /api/detections/{id}/similar`). Tile clicks route through the `onJumpToDetection(id, lat, lon)` prop (GaiaMap's `jumpToDetection`): /similar is global, so a result outside the viewport GeoJSON is fetched via `/enriched` and the map pans/flies to it — no silent no-op. The Review tab's queue rows use the same path. |
| **Prov** | `ProvenancePanel` — full lineage for the selected detection (source raster/chip, model + sensor, calibrated vs raw confidence, detector ensemble, taxonomy). Reads the detection's `metadata` only, no extra API call. See [map-review-similar-provenance.md](map-review-similar-provenance.md). |
| **Active Tracks** | Pass-stitched live tracks; Track Object force-creates a track from the selection |

## Data sources

- `GET /api/detections/{id}/details` + `PUT` (Details tab) — see [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- `GET /api/detections/{id}/similar` (Similar tab)
- `GET /api/detections/{id}/candidate-links` and `POST /api/detection-target-candidates/{id}/approve` (Actions tab)
- `POST /api/analytics/*` (Analytics tab)
- `GET /api/analytics/elevation?lat=&lon=` (Details tab — populates the `ELEV` row in the Geolocation section using the DEM at the detection centroid; falls back to `—` when the DEM is not configured). Requests use `VITE_API_URL` plus `credentials: "include"` at [SelectionPanel.tsx#L185](../../frontend/src/components/map/SelectionPanel.tsx#L185).
- `POST /api/reports/target-package/{id}` (Details tab — the "Generate Target Package" button streams a PDF compiled from already-persisted detection state; see [backend-routers/reports-router.md](../backend-routers/reports-router.md)). Requests use `VITE_API_URL` plus `credentials: "include"` at [SelectionPanel.tsx#L207](../../frontend/src/components/map/SelectionPanel.tsx#L207).

## Label-quality chip (Task 1.2)

The Details-tab header reads the detection title from `displayLabel(props)`
([_helpers.ts](../../frontend/src/components/map/_helpers.ts)) so generic
DOTA-OBB detections surface as e.g. `"Aircraft (generic)"` instead of a
fabricated specific defence label. `labelQuality(props)` drives an inline
`sentinel-tag` chip beside the title:

| `label_quality` | Chip | Tooltip |
|---|---|---|
| `generic`  | `sentinel-tag warn` (`data-testid="label-quality-chip"`) | "Detector emitted a generic class; no specific ontology match without a verifier." |
| `verified` | `sentinel-tag ok`   (`data-testid="label-quality-chip"`) | "Confirmed by RemoteCLIP verifier (semantic_margin meets the configured floor)." |
| `inferred` | — | (default; no chip) |

See [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md)
for the backend policy that resolves both fields.

## Detector provenance chip (Task 1.3)

A third inline chip sits next to the title and surfaces *which detector
produced the call*. It reads `detectionProvenance(props)` from
[_helpers.ts](../../frontend/src/components/map/_helpers.ts), which resolves
`source_layer` and `wbf_member_sources` (top-level or under `metadata`) into a
display-friendly primary name plus any fusion partners.

| State | Chip | Tooltip |
|---|---|---|
| Single-detector, SAM 3 (no `wbf_member_sources`) | `sentinel-tag` (neutral grey) `[Cpu] SAM 3` (`data-testid="detector-provenance-chip"`) | "Single-detector call. SAM 3 text-only on overhead imagery has known limits (LAE-80C: F1 ≤ 28%). Treat as unverified unless corroborated." |
| Single-detector, non-SAM 3 (e.g. DOTA-OBB, CFAR (SAR)) | `sentinel-tag` (neutral grey) `[Cpu] <PRIMARY>` | "Single-detector call. Treat as unverified unless corroborated by a second detector or analyst review." |
| Multi-detector WBF (≥1 partner) | `sentinel-tag info` (blue) `[Cpu] SAM 3 +1` | "Multi-detector agreement: N detectors agreed on this region (WBF). Higher confidence than single-detector calls." |

The `+N` suffix shows the partner count; full partner names live in the
ProvenancePanel "Detector ensemble" block — see
[map-review-similar-provenance.md](map-review-similar-provenance.md). Layer-id
to display-name mapping (`sam3`, `dota_obb`, `grounding_dino`, `yoloe`,
`sar_cfar`) lives in `SOURCE_LAYER_LABELS` inside `_helpers.ts`.

## Failure modes

- Elevation errors are non-blocking and render `--`/unavailable state in the Geolocation section.
- Target-package generation failures keep the user in the panel and surface an inline red error chip (`exportError` state) under the Generate button — previously they only `console.warn`ed and the button silently reset.
- A tab needs **three** things wired or it silently renders empty: (1) an entry in the tab-bar array, (2) a content block in the scroll region (`{rightTab === '<k>' && …}`), and (3) a `setRightTab` case in GaiaMap's `onStepChange` so the product tour lands on populated content. The **Sat** tab had (1) but was missing (2) and (3), so it rendered nothing for every user until both were added.

## Cross-references

- [identification-panel.md](identification-panel.md)
- [object-details-form.md](object-details-form.md)
- [map-analytics-tools.md](map-analytics-tools.md)
- [map-review-similar-provenance.md](map-review-similar-provenance.md)
- [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md)
- [backend/detection-policy.md](../backend/detection-policy.md)
- [product-tour.md](product-tour.md) — the SelectionPanel header chip, collapse button, four tabs, and the Track Object button are first-class Product Tour anchors (`selection-header-chip`, `selection-collapse`, `tab-details` / `tab-analytics` / `tab-similar` / `tab-tracks`, `tracks-track-object`).
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
