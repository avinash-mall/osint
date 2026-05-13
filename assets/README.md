# sentinel-assets

Strict-air-gap static-file service. Hosts the offline raster basemap tile
pyramid and the self-hosted IBM Plex webfonts. Replaces every previous
runtime fetch to `fonts.googleapis.com` and `*.basemaps.cartocdn.com`.

Routed through the main reverse proxy under `/basemap/` and `/assets/`
(see `nginx/tile-proxy.conf`).

## Build prerequisites (connected host, once per asset refresh)

```bash
# 1. Tile pyramid (z=0..10 Carto Dark equivalent) — ~3 GB, 4–8 h.
python scripts/build_offline_basemap.py --zoom 0-10 --out assets/static/basemap

# 2. IBM Plex Sans + Mono woff2 files (latin subset).
bash assets/scripts/fetch_fonts.sh

# 3. Build the image — fails fast if either step above was skipped.
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
