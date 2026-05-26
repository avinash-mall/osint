# GEOM + Prithvi overlay toggles moved into LayerPanel

The Map workspace's top-centre toolbar previously housed three groups in a single tall stack:

1. A wide pill — **GEOM** (HBB / OBB / MASK) + **PRITHVI** (flood / burn / crops) + **tracks**
2. **Draw object** (action button)
3. **Range ring** (action button)
4. **Product Tour** (action button)

That layout is now collapsed: the action buttons sit in a single horizontal row in the top-centre, and the GEOM box-mode + Prithvi overlay toggles live as new sub-sections inside the LayerPanel **Overlays** group. The `tracks` toggle in the pill was redundant (LayerPanel already had an "Active Tracks" `OverlayRow`) and has been removed.

## Why

- **Action verbs vs. display state belong in different places.** Draw / Range ring / Product Tour are *commands* the analyst issues — they begin or end a transient interaction. GEOM mode and Prithvi overlays are *layer-display state* — they change how layers render on the map. Mixing them in one toolbar mirrored neither category cleanly. LayerPanel is already the home of layer-display state (basemap composition, layer toggles, analytics overlays), so GEOM and PRITHVI go there.
- **The tall toolbar covered the detection-counter chip.** The four-row stack was ~180 px and obscured the "N / M detections / last Xm" chip directly underneath ([MapStage.tsx#L839-L843](../../frontend/src/components/map/MapStage.tsx#L839-L843)). Collapsing to a single row exposes the chip; shifting the chip from `top-8` to `top-14` gives it a clean 12 px breather under the new bar.
- **Less noise on the map surface.** The pill carried 7 controls and 2 separators in the operator's line of sight while they're examining imagery. None of those toggles needed to be in front of the map — they all describe how the map is *drawn*, not what to do next.
- **Tour anchors survive unchanged.** Each LayerPanel button keeps the same `data-tour="geom-{k}"` / `data-tour="prithvi-{k}"` attribute the toolbar buttons had. The Product Tour resolves them via DOM lookup, not by component identity, so the migration is invisible to `tourSteps.ts` (only the redundant `tracks-toggle` step was deleted — `[data-tour="layer-toggles"]` already covers the Active Tracks row).

## Trade-off accepted

- **Two more clicks to switch GEOM mode** if the LayerPanel is collapsed. Acceptable — most analysts pick OBB once and stay there; the workflow doesn't demand instant access.
- **One more `OverlayRow` block in the LayerPanel scroll** — adds ~120 px to the panel's content. Already fits inside the panel's scrollable region.

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md) — top-centre action bar + LayerPanel sub-section layout
- [frontend/product-tour.md](../frontend/product-tour.md) — 22-step walkthrough that now spotlights LayerPanel for GEOM/PRITHVI
- [decisions/why-layerpanel-dot-toggle.md](why-layerpanel-dot-toggle.md) — earlier LayerPanel UX decision
- [conventions/coding-style.md](../conventions/coding-style.md) — colocated state, no Redux/Zustand
