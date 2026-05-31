# `frontend/vite.config.ts` — Vite Build And Dev Server

**Path:** [frontend/vite.config.ts](../../frontend/vite.config.ts)
**Lines:** ~49
**Depends on:** Vite 8, React plugin, Tailwind plugin, Cesium plugin

## Purpose

Configures the React/Vite frontend build and the local dev server used outside the Docker Compose stack.

## Why this design

The production SPA is served by nginx from a static Vite build, while local development can proxy `/basemap` to Carto for machines without pre-baked tiles. Production builds use explicit vendor chunks so large geospatial/video dependencies cache separately from Sentinel application code.

## Key symbols

- `plugins` ([vite.config.ts#L7](../../frontend/vite.config.ts#L7)) — React, Tailwind, and Cesium build integration.
- `build.rollupOptions.output.manualChunks` ([vite.config.ts#L10](../../frontend/vite.config.ts#L10)) — splits React, map, graph/3D, video, and miscellaneous vendor code.
- `server.proxy["/basemap"]` ([vite.config.ts#L34](../../frontend/vite.config.ts#L34)) — dev-only basemap proxy; production uses baked assets through nginx.

## Inputs / Outputs

Inputs are source files under `frontend/src/` plus static assets under `frontend/public/`. Output is the production bundle under `frontend/dist/` for the frontend container image.

## Failure modes

- Missing baked fonts still build, but Vite leaves `/assets/fonts/...` URLs for runtime resolution.
- Very large application-only code can still exceed chunk warning thresholds even after vendor chunking; split workspace components if that recurs.
- The `/basemap` proxy is development-only and must not be treated as a runtime online dependency.

## Cross-references

- [frontend/app-and-routing.md](app-and-routing.md)
- [deployment/docker-compose-services.md](../deployment/docker-compose-services.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
