# Why the Offline Basemap Caps at z=14

## Decision

The offline Carto Dark and OpenTopoMap bakes ([scripts/build_offline_basemap.py](../../scripts/build_offline_basemap.py), [scripts/build_offline_terrain.py](../../scripts/build_offline_terrain.py)) now run to **z=14** by default (was z=10). The frontend reference-overlay `TileLayer` caps `maxZoom` and `maxNativeZoom` at the same z=14 ([MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) — `BASEMAP_OVERLAY_MAX_ZOOM`), and the overlay is **unmounted entirely** when the map zooms past it. The LayerPanel disables the opacity slider and shows `Reference hidden past zoom 14 · imagery only` so the user understands the layer didn't break.

## Why

The earlier z=10 ceiling left the overlay useless across the entire context-gathering zoom band. With `maxZoom={20}` and `maxNativeZoom={10}`, Leaflet stretched the z=10 tile up to 2^8 = 256× at z=18 — a single source tile covering ~30 km filled the analyst's viewport with one giant pixel. The bug report screenshot ("pixelated orange-yellow blob across the background") was this stretched tile, not a coordinate-system mismatch. The projection chain is clean: every layer (Leaflet container, Carto/OpenTopoMap XYZ tiles, COG via TiTiler `WebMercatorQuad`, detection geometries via `geojsonToLatLngs`) is on EPSG:3857.

The fix matches the analyst's working zoom split:

| Zoom | Real-world scale     | Used for                                                                |
| ---- | -------------------- | ----------------------------------------------------------------------- |
| z=8  | ~610 m/px (region)   | AOR overview                                                            |
| z=10 | ~150 m/px (city)     | Pass footprint context                                                  |
| z=12 | ~38 m/px (district)  | District-level roads + features                                         |
| **z=14** | **~9.5 m/px (block)** | **Block-level reference; one zoom step out from imagery working zoom**  |
| z=16 | ~2.4 m/px (building) | Object identification — imagery is the source                           |
| z=18 | ~0.6 m/px (vehicle)  | Object identification — imagery is the source                           |

Identification happens on imagery at z=16–18. Context happens on the overlay at z=8–14. z=14 lands the bake ceiling at the boundary between those two modes — one step further (z=15) and the analyst is already in identification territory where the imagery alone is the right answer.

Unmounting (vs. just leaving `maxZoom` set) matters because Leaflet keeps the layer attached past `maxZoom` and just stops fetching new tiles — visually it freezes the last set of z=14 tiles in place as the imagery zooms past them. That looks like a "stuck" overlay. Unmounting the React element cleanly removes it and the LayerPanel can honestly report what happened.

## Size budget

| Asset                   | Before (z=0..10) | After (z=0..14) |
| ----------------------- | ---------------- | --------------- |
| `assets/static/basemap` | ~50 MB           | ~13 GB          |
| `assets/static/terrain` | ~80 MB           | ~22 GB          |
| Combined                | ~130 MB          | **~35 GB**      |

The combined offline assets image grows from ~12 GB to ~47 GB. Acceptable for defence-grade air-gap drops, which typically ship on TB-class removable media. Alternatives considered:

- **z=12 cap (~2 GB combined).** Loses one full doubling of useful context at the airfield-watch scale (z=13 ≈ 19 m/px). Analysts who zoom in to "what's around this hangar" lose the overlay too early.
- **z=16 cap (~3 TB combined).** Impractical to bake or ship; no analyst value above z=14 anyway because imagery is authoritative there.
- **Tile streaming with sparse on-demand fill.** Breaks the air-gap guarantee — explicitly out of scope per [CLAUDE.md hard rule 8](../../CLAUDE.md).

## Resulting behaviour

| Mode    | z=0..14 (with imagery)             | z=15+ (with imagery)              | No imagery loaded   |
| ------- | ---------------------------------- | --------------------------------- | ------------------- |
| SAT     | imagery only                       | imagery only                      | Carto fallback 100% |
| BASE    | imagery + Carto overlay (z<=14)    | imagery only + autohide hint      | Carto fallback 100% |
| TERRAIN | imagery + Terrain overlay (z<=14)  | imagery only + autohide hint      | Terrain fallback 100% |

The cartographic-fallback block (`!selectedImageryData`) still uses `maxNativeZoom={10}` — when there is no imagery, the analyst wants a basemap regardless of zoom, even if stretched. Treating that block is a follow-up question, not part of this change.

## Partial bakes: parent-tile fallback (follow-up, shipped)

The table above assumes the bake actually reaches z=14. In practice a host's
pyramid can stop short (the long-running bake pipeline is incremental — one
dev host shipped basemap z≤11 / terrain z≤10), and then **every** native tile
request in the z12–14 band 404s: the BASE/TERRAIN overlay went completely
blank exactly in the analyst's working zoom band, which read as "the basemap
is broken". Fix: all three reference layers (fallback + both overlays) render
via [`FallbackTileLayer`](../../frontend/src/components/map/FallbackTileLayer.tsx)
— a vendored ES-module port of Leaflet.TileLayer.Fallback (BSD-2-Clause) that,
on tile error, substitutes the parent tile (z−1, scaled ×2, cropped to the
covering quadrant), recursively down to z0. Worst case inside the z≤14 band
is an 8× stretch (z14 over a z11 bake) — a far cry from the 256× blob this
decision was written to kill, and it only engages when native tiles are
missing: a fully-baked host behaves exactly as before. The SAT/TiTiler
imagery layer deliberately does **not** use it — tiles outside the COG
footprint legitimately 404, and a parent fallback would smear a stretched
low-zoom blob around the imagery edges. Vendored as an ES module (not the
upstream UMD file) for the same global-`L` bundler hazard documented in
[why-detection-mvt-tiles.md](why-detection-mvt-tiles.md).

## Cross-references

- [why-basemap-overlay-composition.md](why-basemap-overlay-composition.md) — predecessor: the zIndex stack this builds on
- [why-sat-tiles-cap-at-native-zoom.md](why-sat-tiles-cap-at-native-zoom.md) — analogous policy for the SAT `TileLayer`
- [../scripts/build-offline-basemap.md](../scripts/build-offline-basemap.md)
- [../scripts/build-offline-terrain.md](../scripts/build-offline-terrain.md)
- [../frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
