# sentinel-assets

Strict-air-gap static-file service. Hosts the offline raster basemap tile
pyramid and the self-hosted IBM Plex webfonts. Replaces every previous
runtime fetch to `fonts.googleapis.com` and `*.basemaps.cartocdn.com`.

Routed through the main reverse proxy under `/basemap/` and `/assets/`
(see `nginx/tile-proxy.conf`).

## Build (connected host)

Both fetchers run inside the Dockerfile, so the only command needed is:

```bash
docker compose up -d --build assets       # or just `docker compose up -d --build`
```

First build is slow — ~3 GB / 4–8 h for the full z=0..10 pyramid. A
BuildKit cache mount (`/cache/basemap`) persists the fetched tiles
across rebuilds, so subsequent builds complete in seconds.

For a quick smoke build, override the zoom range via build arg:

```bash
BASEMAP_ZOOM=0-4 docker compose up -d --build assets   # ~340 tiles, <1 min
```

### Out-of-band pre-bake (optional)

The host-side scripts still work standalone if you want progress
visibility outside `docker build`, or to stage tiles on a different
machine. Anything present under `assets/static/` at build time is
copied into the cache before fetching, so the idempotent fetchers
skip it:

```bash
python scripts/build_offline_basemap.py --zoom 0-10 --out assets/static/basemap
bash assets/scripts/fetch_fonts.sh
docker compose build assets
```

## Layout

```
assets/
  Dockerfile           # FROM nginx:1.27.3-alpine; asserts tiles+fonts exist
  nginx.conf           # /basemap/, /fonts/, /healthz, /LICENSE.txt
  scripts/
    fetch_fonts.sh     # pulls IBM Plex woff2 from github.com/IBM/plex
  static/
    basemap/           # populated by build_offline_basemap.py (gitignored)
    fonts/             # populated by fetch_fonts.sh (gitignored)
    LICENSE.txt        # SIL OFL 1.1 (IBM Plex)
    .build-metadata.json
```

## Refreshing only the assets image

The reverse proxy caches the assets upstream for 30 days. After rebuilding:

```bash
docker compose build assets
docker compose up -d assets
docker compose restart nginx   # flush the proxy_cache
```
