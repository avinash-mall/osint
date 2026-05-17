# syntax=docker/dockerfile:1.7
#
# Air-gap assets image: serves the offline basemap pyramid, OpenTopoMap
# terrain pyramid, and IBM Plex webfonts. The fetchers run inside the
# build, so a fresh clone needs only `docker compose up -d --build` — no
# host-side pre-bake required.
#
# BuildKit cache mounts (/cache/basemap, /cache/terrain) persist the
# multi-GB tile fetches across rebuilds; only the first build pays the
# full wallclock cost.
#
# Override either zoom range for a quick smoke build:
#   BASEMAP_ZOOM=0-4 TERRAIN_ZOOM=0-4 docker compose up -d --build assets
#
# Build context is the repo root (compose passes `context: .`) so the
# fetcher scripts under scripts/ are reachable.

# ---------------------------------------------------------------- fetcher
FROM python:3.11-slim AS fetcher

ARG BASEMAP_ZOOM=0-10
ARG BASEMAP_CONCURRENCY=16
ARG TERRAIN_ZOOM=0-10
ARG TERRAIN_CONCURRENCY=4
ARG PLEX_VERSION=@ibm/plex-sans@1.1.0
ARG PLEX_MONO_VERSION=@ibm/plex-mono@1.1.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates bash \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY scripts/build_offline_basemap.py /build/build_offline_basemap.py
COPY scripts/build_offline_terrain.py /build/build_offline_terrain.py
COPY assets/scripts/fetch_fonts.sh    /build/fetch_fonts.sh
COPY assets/static/                   /seed/

# Mirror the repo layout fetch_fonts.sh assumes (it resolves paths
# relative to its own location: ASSETS_DIR=$SCRIPT_DIR/..).
RUN mkdir -p /work/static \
             /stage/assets/scripts \
             /stage/assets/static/fonts \
 && cp /build/fetch_fonts.sh /stage/assets/scripts/fetch_fonts.sh \
 && chmod +x /stage/assets/scripts/fetch_fonts.sh \
 && if [ -d /seed/fonts ]; then cp -an /seed/fonts/. /stage/assets/static/fonts/ || true; fi

# Basemap tiles — into a cache mount so re-runs are seconds, then
# mirrored into /work for stage 2 to COPY.
RUN --mount=type=cache,target=/cache/basemap \
    if [ -d /seed/basemap ]; then cp -an /seed/basemap/. /cache/basemap/ || true; fi \
 && python /build/build_offline_basemap.py \
        --zoom        "${BASEMAP_ZOOM}" \
        --concurrency "${BASEMAP_CONCURRENCY}" \
        --out         /cache/basemap \
 && mkdir -p /work/static/basemap \
 && cp -an /cache/basemap/. /work/static/basemap/

# Terrain tiles — separate cache mount, lower concurrency by default
# because OpenTopoMap rate-limits more aggressively than CARTO.
RUN --mount=type=cache,target=/cache/terrain \
    if [ -d /seed/terrain ]; then cp -an /seed/terrain/. /cache/terrain/ || true; fi \
 && python /build/build_offline_terrain.py \
        --zoom        "${TERRAIN_ZOOM}" \
        --concurrency "${TERRAIN_CONCURRENCY}" \
        --out         /cache/terrain \
 && mkdir -p /work/static/terrain \
 && cp -an /cache/terrain/. /work/static/terrain/

# Fonts — tiny payload, no cache mount needed.
RUN PLEX_VERSION="${PLEX_VERSION}" PLEX_MONO_VERSION="${PLEX_MONO_VERSION}" \
        bash /stage/assets/scripts/fetch_fonts.sh \
 && cp -an /stage/assets/static/. /work/static/

# ------------------------------------------------------------------ final
FROM nginx:1.27.3-alpine

COPY assets/nginx.conf            /etc/nginx/conf.d/default.conf
COPY --from=fetcher /work/static/ /usr/share/nginx/html/

RUN test -f /usr/share/nginx/html/basemap/0/0/0.png \
 && test -f /usr/share/nginx/html/terrain/0/0/0.png \
 && test -f /usr/share/nginx/html/fonts/ibm-plex-sans-400.woff2 \
 && test -s /usr/share/nginx/html/basemap/ATTRIBUTION.txt \
 && test -s /usr/share/nginx/html/terrain/ATTRIBUTION.txt

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1

EXPOSE 80
