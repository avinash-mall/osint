# `frontend/src/components/admin/ReferencePlatformsView.tsx` — Reference DB browser

**Path:** [frontend/src/components/admin/ReferencePlatformsView.tsx](../../frontend/src/components/admin/ReferencePlatformsView.tsx)
**Lines:** ~667
**Depends on:** `axios`, `lucide-react`, [useEventStream](event-stream-hook.md) (`reference-seed` topic), [apiError.ts](../../frontend/src/utils/apiError.ts), backend `GET /api/reference-platforms*` + `GET /api/reference-chips/{id}/image` + `POST /api/admin/reference/seed`.

## Purpose

Admin tab listing curated reference platforms with family/country filters. Clicking a platform shows its detail + chip thumbnails. Used to audit the bake's output and the licence/attribution metadata.

## Why this design

- Two-column grid: list left, detail right. Reads like a file-explorer.
- Filters are exact-match by family or country_of_origin — keeps the SQL fast and avoids ambiguous fuzzy-match.
- Chip thumbnails fetched via the shared [ChipImg](chip-img-component.md) component, which wraps the chip-serving route (path-traversal-guarded — see [reference-platforms-router.md](../backend-routers/reference-platforms-router.md)) and renders a neutral `✕` placeholder on load failure.
- Pagination caps at limit=200 by default. The list response now exposes `total` (total filtered count) alongside `count` (page length); when `total > platforms.length` the list header shows a "Showing N of M" affordance so analysts can tell the list is truncated. The route supports up to 1000 and offsets if a future tab needs a full paginator.

## Key symbols

- `ReferencePlatformsView({ onCount })` — default export. Registered in [AdminScreen](workspace-admin.md).
- `load()` — fetches `GET /api/reference-platforms?family=&country=&limit=200`.
- `openPlatform(id)` — fetches `GET /api/reference-platforms/{id}` and renders the detail column. Guarded by a monotonic request token so a slow earlier response can't overwrite a later selection's detail (rapid row clicks).
- `triggerSeed(force)` — `POST /api/admin/reference/seed`; progress arrives over WS topic `reference-seed`. `seedBusy` clears on `done` (incl. `{skipped:true}` from the worker's idempotency guard → "already seeded" notice), on `error`, or via a 10 s fallback timeout when no event arrives (worker down / pre-skipped-event worker) so the Seed button can't hang disabled forever.

## Inputs / Outputs

- Inputs: `onCount?: (n: number) => void` — for the NAV badge.
- Outputs: visual UI; no mutations from this view.

## Failure modes

- 401 → no session.
- Filter-input typos → empty list (the route uses exact-match; "usa" won't match "USA").

## Cross-references

- Backend router: [reference-platforms-router.md](../backend-routers/reference-platforms-router.md)
- Schema: [reference-platform-db.md](../backend/reference-platform-db.md)
- Plan E spec (in-repo): [docs/superpowers/plans/2026-05-27-reference-db-plan-e-frontend.md](../superpowers/plans/2026-05-27-reference-db-plan-e-frontend.md)
