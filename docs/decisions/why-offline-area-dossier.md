# Decision — the area dossier is offline, from baked `ne_countries`

## Context

ShadowBroker's right-click "country dossier" pulls head-of-state, Wikipedia
summary, and latest imagery from the live internet. Sentinel must run air-gapped
(Hard rule #8). The transferable idea — *right-click anywhere for instant area
context* — is worth keeping; the online sources are not.

## Decision

`GET /api/dossier?lat=&lon=` resolves the country at the point by
**point-in-polygon over the locally-baked `ne_countries` table**
(`ST_Contains`), returning name / admin / ISO3 / population / GDP, plus a count
of Sentinel's own detections within 25 km (`ST_DWithin`). The frontend opens it
from a map **right-click** (contextmenu) as a Leaflet popup.

No Wikipedia, no Wikidata, no head-of-state lookup, no network of any kind —
every field comes from data already on the box.

## Why

- **Air-gap first.** `ne_countries` is already loaded by `init_postgis.sql` (and
  replaceable with a fuller Natural Earth import) and already backs the basemap
  overlay, so the dossier reuses an existing offline dataset rather than adding
  a feed.
- **Reuses existing spatial primitives.** Same `ST_Contains` / `ST_DWithin`
  patterns used elsewhere; no new tables, no new dependency.
- **Scoped to what the data supports.** We deliberately omit head-of-state /
  prose summaries because there is no offline authority for them; padding the
  dossier with stale baked text would mislead more than help. Population/GDP are
  shown because they are present and slow-changing.

## Consequences

- Dossier richness is bounded by the `ne_countries` columns. An operator who
  wants more (e.g. capital, subregion) imports a richer Natural Earth dataset
  into the same table — the endpoint surfaces whatever columns exist.
- Points in international waters return `country: null` (handled in the UI).
- Clean-room — no ShadowBroker (AGPL) source copied; only the interaction idea.

## Cross-references

- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md) (`/api/dossier`)
- [backend/init_postgis.sql](../../backend/init_postgis.sql) (`ne_countries` schema + seed)
- Tests: [backend/tests/test_dossier_route.py](../../backend/tests/test_dossier_route.py)
- Service: [frontend/src/services/dossier.ts](../../frontend/src/services/dossier.ts)
