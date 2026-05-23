# Nginx Gateway & Tile Cache

**Sources:** [nginx/](../../nginx/), [docker-compose.yml](../../docker-compose.yml) (nginx service block)

## Purpose

Single TLS-terminating reverse proxy routing every path. Single tile cache for all tile traffic (24 h TTL). Single FMV HLS endpoint.

## Route table

| Path prefix | Upstream | Notes |
|---|---|---|
| `/` | `frontend:3000` | Vite-built SPA |
| `/api/` | `backend:8080` | All REST endpoints |
| `/ws` | `backend:8080` | WebSocket upgrade |
| `/tiles/` | `titiler:8080` | 24 h `proxy_cache` |
| `/maps/` | `martin:3000` | 24 h `proxy_cache` |
| `/basemap/` | `assets:80` | offline Carto Dark, z=0..14 |
| `/terrain/` | `assets:80` | offline OpenTopoMap, z=0..14 |
| `/assets/` | `assets:80` | IBM Plex webfonts, basemap attribution, license bundle |
| `/fmv/` | filesystem (HLS segments under `/data/fmv/<clip_id>/`) | served from the mounted FMV volume directly |

## Cache

- `proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=tiles:100m max_size=10g inactive=24h use_temp_path=off;`
- Applied to `/tiles/`, `/maps/`, `/basemap/`, `/terrain/`, `/assets/fonts/`.
- `200` responses cached 24 h on `/tiles/`; `proxy_cache_use_stale` serves stale on upstream error/timeout.

### `/tiles/` cold-cache + refresh tuning

- `proxy_cache_lock on` collapses a burst of identical cold-cache requests into one upstream fetch. `proxy_cache_lock_timeout` is **2 s** (was 5 s): a waiting request falls through to its own TiTiler call after 2 s instead of stalling the whole viewport behind one slow fetch.
- `proxy_cache_background_update on` serves the stale (expired) tile immediately, refreshes it in the background → a re-visited pass paints instantly once the 24 h TTL lapses. Pairs with the `updating` flag in `proxy_cache_use_stale`.
- Rationale: [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).

## TLS

`FORCE_HTTPS=1` toggles redirect of port 80 → 443 and adds HSTS. Default: plain HTTP on port 3000 (development) — operators terminate TLS at the upstream load balancer or set `FORCE_HTTPS=1` + mount certs.

## Cross-references

- [architecture/service-topology.md](../architecture/service-topology.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
- [operations/health-monitoring.md](../operations/health-monitoring.md) (nginx healthcheck path)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md) (SAT TileLayer that consumes `/tiles/`)
- [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md)
