# Nginx Gateway & Tile Cache

**Sources:** [nginx/](../../nginx/), [docker-compose.yml](../../docker-compose.yml) (nginx service block)

## Purpose

Single TLS-terminating reverse proxy that routes every path. Single tile cache for all tile traffic (24 h TTL). Single FMV HLS endpoint.

## Route table

| Path prefix | Upstream | Notes |
|---|---|---|
| `/` | `frontend:3000` | Vite-built SPA |
| `/api/` | `backend:8080` | All REST endpoints |
| `/ws` | `backend:8080` | WebSocket upgrade |
| `/tiles/` | `titiler:8080` | 24 h `proxy_cache` |
| `/maps/` | `martin:3000` | 24 h `proxy_cache` |
| `/basemap/` | `assets:80` | offline Carto Dark, z=0..10 |
| `/assets/` | `assets:80` | IBM Plex webfonts, basemap attribution, license bundle |
| `/fmv/` | filesystem (HLS segments under `/data/fmv/<clip_id>/`) | served from the mounted FMV volume directly |

## Cache

- `proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=tilecache:10m max_size=2g inactive=24h use_temp_path=off;`
- Applied to `/tiles/` and `/maps/`.
- Bypassable via `proxy_cache_bypass $http_pragma` for cache-busting during dev.

## TLS

`FORCE_HTTPS=1` toggles redirect of port 80 → 443 and adds HSTS. By default, traffic is plain HTTP on port 3000 (development) — operators terminate TLS at the upstream load balancer or set `FORCE_HTTPS=1` + mount certs.

## Cross-references

- [architecture/service-topology.md](../architecture/service-topology.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
- [operations/health-monitoring.md](../operations/health-monitoring.md) (nginx healthcheck path)
