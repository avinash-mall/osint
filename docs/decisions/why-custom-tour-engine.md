# Custom in-repo tour engine (no react-joyride / intro.js / shepherd)

The map workspace ships its own ~315-line `ProductTour` component instead of pulling in `react-joyride`, `intro.js`, `shepherd.js`, `@reactour/tour`, or any other onboarding library.

## Why

- **Air-gap policy.** Sentinel is deployed offline ([deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)). Any npm package would bundle locally so that is not a blocker, but the project repeatedly avoids plugins when ~200–300 lines of custom code suffices — see [decisions/temporal-swipe-comparator.md](temporal-swipe-comparator.md) (custom Leaflet pane + `clip-path` instead of a swipe plugin) and [decisions/manual-draw-modal-replaces-prompt.md](manual-draw-modal-replaces-prompt.md) (in-repo modal instead of a dialog library).
- **Theme parity.** All chrome on the map workspace uses the `sentinel-*` Tailwind tokens and the `.confirm-overlay` / `.confirm-dialog` / `.btn` CSS classes from [atoms.tsx](../../frontend/src/components/atoms.tsx). A library tour would either drag in its own CSS (a second design language on the same page) or require heavy override work to match. The in-repo engine reuses the existing modal markup verbatim.
- **No `forwardRef`/portal dance.** Library tours typically demand React refs or portals to attach to targets. The in-repo engine uses `data-tour="<id>"` HTML attributes and `document.querySelector` — components own their tour identity, refactors don't break the tour, and components that don't exist yet (e.g. `SelectionPanel`, which only mounts when a detection is selected) are auto-skipped instead of throwing.
- **Bundle weight.** `react-joyride` adds ~25 KB minified to a chunk that is already past Vite's 500 KB warning threshold (the map workspace bundle is currently ~1.2 MB). The custom engine is dead-code-friendly and only compiles what is referenced.
- **Behavioural fit.** Most library tours assume a "linear, can-not-skip" or "step-must-complete" flow. Sentinel needs **fault-tolerant** stepping: panels may be collapsed, the SelectionPanel only renders on selection, focus mode collapses chrome to the viewport edges. Auto-skipping missing targets is a few lines of code in the custom engine; in a library it would mean fighting the framework.

## Trade-off accepted

- **More code to maintain in-repo.** ~315 LOC for `ProductTour.tsx` plus ~90 LOC for the hook and ~190 for the step list. Acceptable because the engine is bounded (no state machine beyond `running` / `stepIndex` / `welcomeOpen`) and the step list is the only thing that grows.
- **No beacon / pulse animation out of the box.** The spotlight is a static box-shadow inverse cutout; if the project later wants a pulsing beacon at the target, that's a CSS animation on the spotlight `<div>`, not a library swap.

## Cross-references

- [frontend/product-tour.md](../frontend/product-tour.md) — the implementation this decision justifies.
- [decisions/temporal-swipe-comparator.md](temporal-swipe-comparator.md)
- [decisions/manual-draw-modal-replaces-prompt.md](manual-draw-modal-replaces-prompt.md)
- [conventions/coding-style.md](../conventions/coding-style.md)
