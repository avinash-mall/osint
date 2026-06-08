# Product Tour — Map workspace onboarding

**Paths:**
- [frontend/src/hooks/useProductTour.ts](../../frontend/src/hooks/useProductTour.ts) (~87 lines)
- [frontend/src/components/tour/tourSteps.ts](../../frontend/src/components/tour/tourSteps.ts) (~361 lines)
- [frontend/src/components/tour/ProductTour.tsx](../../frontend/src/components/tour/ProductTour.tsx) (~334 lines)

**Lines:** ~590 total across three files

**Depends on:** React 19 hooks, `lucide-react` icons (`HelpCircle`, `X`), `.confirm-overlay` / `.confirm-dialog` / `.btn` CSS classes from [index.css](../../frontend/src/index.css), CSS variables `--accent` / `--bg-1` / `--text` / `--muted`, the `data-tour="<id>"` attributes scattered across [MapStage.tsx](../../frontend/src/components/map/MapStage.tsx), [LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx), [SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx) and [TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx).

## Purpose

In-app guided onboarding for the Map workspace, aimed at defence analysts seeing Sentinel for the first time. Three behaviours:

1. **Auto-welcome on first visit.** When the operator opens the map page and `localStorage[sentinel:tour-completed]` is unset, a welcome modal pops with three actions: *Take the tour*, *Maybe later* (dismiss without setting the flag — re-pops next visit), *Don't show again* (sets the flag).
2. **Manual re-launch.** A **Product Tour** button in the top-center toolbar of `MapStage` re-opens the welcome modal any time.
3. **Step-by-step tooltip walkthrough.** 50 declarative steps (one per interactive control on the page) covering — left rail (basemap, opacity, layer toggles, GEOM modes, PRITHVI overlays, detection classes, imagery passes, imagery delete, analytics-tools layer toggles), top action bar (Draw / Range-ring / Product Tour), zoom cluster (zoom-in/out, recenter, focus mode, tactical visual mode), Time-machine deep (play, recenter, ranges, CONF threshold, passes count, sensor legend), bottom chrome (restored-hidden filter banner, SHOWING N/M suppression chip, event timeline + window buttons + in-window counter), and SelectionPanel deep (header status chip, collapse, six tabs incl. the `tab-satellites` overpass-planning tab and the `tab-provenance` detection-lineage tab, three Analytics tools + capabilities footer, Track Object button). Each step body is a two-sentence what-it-does + when-an-analyst-reaches-for-it explanation.

## Why this design

- **No new npm dependency.** Sentinel ships air-gapped and bundles every dependency at build time. The project has a precedent of building small custom UI primitives rather than pulling plugins — see [decisions/temporal-swipe-comparator.md](../decisions/temporal-swipe-comparator.md) and [decisions/manual-draw-modal-replaces-prompt.md](../decisions/manual-draw-modal-replaces-prompt.md). A custom ~315-line tour engine reuses the existing `.confirm-overlay` / `.confirm-dialog` markup from [atoms.tsx](../../frontend/src/components/atoms.tsx) so welcome-modal styling matches every other modal in the app.
- **Declarative step list.** `TOUR_STEPS` is a pure array of `{ id, selector, title, body, placement }`. Adding or re-ordering a step is a single-record edit; no rendering code changes.
- **`data-tour` attribute lookup.** Tour anchors are decoupled from React component identity. Components own their `data-tour="..."` strings; the tour engine queries the DOM with `document.querySelector`. Refactors that move components don't break the tour, and panels not currently mounted (e.g. `SelectionPanel`, only rendered when a detection is selected) are simply auto-skipped.
- **Inverse-cutout spotlight.** The highlight is one positioned `<div>` matched to the target's `getBoundingClientRect()` with `box-shadow: 0 0 0 9999px rgba(0,0,0,0.55)` — no canvas, no SVG mask, no extra DOM. `pointer-events: none` on the backdrop so the analyst can still click the highlighted control directly.
- **Persistence via the `sentinel:*` localStorage convention.** Mirrors [usePreferences.tsx](../../frontend/src/hooks/usePreferences.tsx). Try/catch fallback for private mode.
- **Why the tour can fire above Leaflet chrome.** The map workspace uses `z-index: 500` for floating panels and Leaflet panes max at `z: 700`. The tour overlay sits at `z: 1000` so the spotlight + tooltip always render above.

## Key symbols

### `useProductTour.ts`
- `LS_KEY = 'sentinel:tour-completed'` ([useProductTour.ts#L15](../../frontend/src/hooks/useProductTour.ts#L15)) — single persistence key.
- `useProductTour(): ProductTourState` ([useProductTour.ts#L31-L86](../../frontend/src/hooks/useProductTour.ts#L31-L86)) — auto-opens the welcome modal on mount when the LS key is absent. Returns `{ running, stepIndex, welcomeOpen, start, next, prev, finish, skip, dismissWelcome, launchFromButton }`.
- `dismissWelcome` ≠ `skip`: *dismiss* closes the modal without setting the LS flag (re-pops next session); *skip* persists the flag.

### `tourSteps.ts`
- `TOUR_STEPS: TourStep[]` ([tourSteps.ts#L29-L403](../../frontend/src/components/tour/tourSteps.ts#L29-L403)) — 50 steps in display order. Selectors are `[data-tour="..."]` matches.
- **`onStepChange` callback** ([ProductTour.tsx](../../frontend/src/components/tour/ProductTour.tsx)) — optional prop the engine fires whenever the resolved step changes (or with `null` when the tour is not running). [GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx) implements it to open the SelectionPanel + switch to the right tab before a Details/Analytics/**Satellites**/Similar/**Provenance**/Tracks step is spotlighted, and to open the timeline panel before any `tm-*` / `event-*` step. Each `tab-<k>` step needs its own `setRightTab('<k>')` case here, or the spotlight lands on an empty pane (the `tab-satellites` case was missing). This is what lets steps that live inside a panel/tab the analyst hasn't visited still resolve cleanly: the parent satisfies prerequisite state, then the auto-skip effect retries `document.querySelector` on the next render.
- `Placement` ([tourSteps.ts#L11](../../frontend/src/components/tour/tourSteps.ts#L11)) — `'top' | 'bottom' | 'left' | 'right'`, the *preferred* tooltip placement (falls back automatically if the card would clip the viewport).

### `ProductTour.tsx`
- `pickPlacement(rect, preferred)` ([ProductTour.tsx#L34-L66](../../frontend/src/components/tour/ProductTour.tsx#L34-L66)) — tries the preferred placement, then the other three; clamps into the viewport as a last resort.
- `computeAnchor(step, target)` ([ProductTour.tsx#L68-L81](../../frontend/src/components/tour/ProductTour.tsx#L68-L81)) — derives card position + spotlight rect from a single `getBoundingClientRect()`.
- Auto-skip effect ([ProductTour.tsx#L101-L113](../../frontend/src/components/tour/ProductTour.tsx#L101-L113)) — when the current step's target is missing, calls `next()` or `prev()` based on the last-moved direction; bounded by `TOUR_STEPS.length` so it terminates at either end.
- Anchor re-compute ([ProductTour.tsx#L116-L132](../../frontend/src/components/tour/ProductTour.tsx#L116-L132)) — `useLayoutEffect` listens for `resize` and `scroll` (capture-phase) so the card stays glued to the target.
- Keyboard shortcuts ([ProductTour.tsx#L135-L154](../../frontend/src/components/tour/ProductTour.tsx#L135-L154)) — `Esc` skip, `→` next, `←` prev during the tour; `Esc` "Maybe later" in the welcome modal.

## Inputs / Outputs

- **Input:** none (besides the existence of `[data-tour="..."]` anchors in the DOM).
- **Output:** sets `localStorage[sentinel:tour-completed] = '1'` on Finish, Skip, or "Don't show again".
- **Component contract:** [GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx) calls `const tour = useProductTour()`, passes `tour.launchFromButton` to `MapStage` as the optional `onLaunchTour` prop, and renders `<ProductTour state={tour} />` as the last child of the workspace div.

## Failure modes

- **localStorage unavailable** (private browsing, quota): both read and write silently no-op via try/catch — the tour does not auto-pop, but the manual button still works and the tour itself runs normally.
- **`data-tour` anchor missing from the DOM** (panel collapsed, SelectionPanel unmounted, focus mode hides chrome): the auto-skip effect advances in the operator's last-moved direction until a valid step or the end of the list. Worst case is the tour ends cleanly without visiting unreachable steps.
- **Window resize during a step**: the anchor re-computes via `useLayoutEffect`, so the tooltip and spotlight follow.
- **A step targeting an element that scrolls out of view inside a panel**: the spotlight and tooltip will reposition on `scroll`-capture, but if the operator hides the panel entirely (e.g. collapses the LayerPanel rail), the next render's `document.querySelector` will return null and the step will auto-skip.

## Cross-references

- [decisions/why-custom-tour-engine.md](../decisions/why-custom-tour-engine.md) — rationale for not adding `react-joyride` / `intro.js` / `shepherd`.
- [map-stage-and-layers.md](map-stage-and-layers.md) — host of the Product Tour button.
- [decisions/temporal-swipe-comparator.md](../decisions/temporal-swipe-comparator.md) — precedent for in-repo UI instead of plugins.
- [decisions/manual-draw-modal-replaces-prompt.md](../decisions/manual-draw-modal-replaces-prompt.md) — precedent for custom modal markup.
- [conventions/coding-style.md](../conventions/coding-style.md) — "No Redux / Zustand — state colocated in the component that owns it" justifies the per-page `useProductTour` hook instead of a global tour provider.
