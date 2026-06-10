# Why detections stream onto the map live (preview-then-reconcile)

**Status:** accepted
**Date:** 2026-06-10
**Scope:** `backend/worker_legacy.py` (`store_detections`, `_store_chip`), `frontend/src/components/GaiaMap.tsx`

## Decision

Detections now appear on the map **as each chip is detected**, instead of after
the whole ~90 s pass. The worker already stored detections per chip and fired a
`detections_partial` WS event per chip — we extended that event to carry
**map-ready GeoJSON features**, and the map appends them live. When the pass
finishes, the authoritative, fully-enriched set is loaded once and replaces the
live preview (reconciliation).

## Why this design

**Preview-then-reconcile.** The per-chip event carries a *compact* feature
(`id`, `class`, `confidence`, `calibrated_confidence`, `pass_id`, polygon
geometry, `live_preview: true`) built from the in-memory detection dict —
`store_detections` back-fills `det["id"]` from its `RETURNING id` so no re-query
is needed. The full feature shape (parent_class, review_status, threshold,
provenance, uncertainty halo) still comes from `GET /api/detections/geojson` on
the final `detections_updated` event. So the live view is a fast preview; the end
state is authoritative and identical to before.

**Why embed features (vs. re-poll per chip).** Embedding makes the map
**append-only** with zero extra round-trips — each chip's `setDetectionsGeoJSON`
spreads in the new features (deduped by `properties.id`). Re-polling
`/api/detections/geojson` per chip would need a new `pass_id` filter and
re-serialize a growing set 25× (more DB load, map flicker).

**Framing.** On the first chip of a pass the map auto-selects it and loads its
imagery (the COG exists ~0.5 s into processing) — the same auto-select the final
`ingest_succeeded` event already did, just earlier — so the streaming detections
land in view.

## Bounds & caveats

- **Size cap.** `LIVE_DETECTIONS_MAX_FEATURES` (default 400): a chip with more
  detections streams counts only; the end-of-pass load still shows them. Keeps WS
  messages small.
- **Kill switch.** `LIVE_DETECTIONS_STREAM=0` falls back to count-only events +
  the end-of-pass load (the prior behaviour).
- **WBF defer mode.** When cross-chip fusion is WBF (`_WeightedBoxFusionIndex`,
  `defer_streaming_store`), per-chip store is deferred to the final flush, so
  features arrive in one burst near the end rather than live. The default
  (`obb_nms`) streams live.

## Cross-references

- [operations/websocket-event-channels.md](../operations/websocket-event-channels.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [frontend/event-stream-hook.md](../frontend/event-stream-hook.md)
- [decisions/why-detection-boxes-use-polygon-map.md](why-detection-boxes-use-polygon-map.md)
