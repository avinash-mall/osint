# `frontend/src/components/admin/ReferencePlatformsView.tsx` — Reference DB browser

**Path:** [frontend/src/components/admin/ReferencePlatformsView.tsx](../../frontend/src/components/admin/ReferencePlatformsView.tsx)
**Lines:** ~503
**Depends on:** `axios`, `lucide-react`, backend `GET /api/reference-platforms*` + `GET /api/reference-chips/{id}/image`.

## Purpose

Admin tab listing curated reference platforms with family/country filters. Clicking a platform shows its detail + chip thumbnails. Used to audit the bake's output and the licence/attribution metadata.

## Why this design

- Two-column grid: list left, detail right. Reads like a file-explorer.
- Filters are exact-match by family or country_of_origin — keeps the SQL fast and avoids ambiguous fuzzy-match.
- Chip thumbnails fetched via the chip-serving route, which enforces a path-traversal guard. See [reference-platforms-router.md](../backend-routers/reference-platforms-router.md).
- Pagination caps at limit=200 by default. The route supports up to 1000 and offsets but the UI doesn't surface a paginator yet — DOTA's 18 platforms easily fit; Plan F can add pagination when xView lands.

## Key symbols

- `ReferencePlatformsView({ onCount })` — default export. Registered in [AdminScreen](workspace-admin.md).
- `load()` — fetches `GET /api/reference-platforms?family=&country=&limit=200`.
- `openPlatform(id)` — fetches `GET /api/reference-platforms/{id}` and renders the detail column.

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
