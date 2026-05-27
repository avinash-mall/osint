# `frontend/src/components/ChipImg.tsx` — Reference-chip thumbnail

**Path:** [frontend/src/components/ChipImg.tsx](../../frontend/src/components/ChipImg.tsx)
**Lines:** ~60
**Depends on:** React, the backend chip-serving route at `GET /api/reference-chips/{chip_id}/image` (see [reference-platforms-router.md](../backend-routers/reference-platforms-router.md)).

## Purpose
Single React component for rendering a reference-chip thumbnail. Wraps the `<img>` + onError fallback so both consuming sites (IdentificationPanel + ReferencePlatformsView) share one implementation. On image-load failure, displays a neutral SVG placeholder with `✕` glyph + `aria-label="chip image unavailable"`.

## Why this design
- **Single source of truth for the chip URL** (`${API_URL}/api/reference-chips/{id}/image`).
- **Trust signal**: an analyst should never approve a candidate without realising the chip evidence is missing — the explicit failure state makes that obvious.
- **React-state fallback** instead of mutating `<img>` DOM attributes in onError — cleaner, easier to test, easier to extend with additional fallback affordances later.

## Key symbols
- `ChipImg({ chipId, size, alt, className, style })` — default export.

## Inputs / Outputs
- Inputs: `chipId` (UUID string), optional `size` (default 32 px), optional `alt`/`className`/`style`.
- Outputs: `<img>` element on success; `<span>` placeholder on error.

## Failure modes
- 4xx/5xx from `/api/reference-chips/{id}/image` → onError sets state, renders the placeholder.
- Empty `chipId` prop → renders the image element with a broken src, which will trigger the same fallback path. Not currently guarded; future enhancement.

## Cross-references
- Consumers: [identification-panel.md](identification-panel.md), [admin-reference-platforms.md](admin-reference-platforms.md).
- Backend route: [reference-platforms-router.md](../backend-routers/reference-platforms-router.md).
- Plan F spec (in-repo): [docs/superpowers/plans/2026-05-27-reference-db-plan-f-websocket-sync.md](../superpowers/plans/2026-05-27-reference-db-plan-f-websocket-sync.md).
