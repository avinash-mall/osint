# syntax=docker/dockerfile:1.7
#
# Air-gap DEM bake image. Downloads the worldwide Copernicus GLO-30 DEM
# (~150 GB across ~26,000 1° GeoTIFF tiles) into the image at build time
# and rsyncs it onto the `dem_data` named volume on container start, then
# exits. The runtime backend + worker services mount that volume read-only
# at /data/dem.
#
# BuildKit cache mount (/cache/dem) persists the multi-day fetch across
# rebuilds; only the first build pays the full wallclock cost.
#
# Override the fetch range for a faster regional smoke build:
#   DEM_LAT_MIN=20 DEM_LAT_MAX=35 DEM_LON_MIN=50 DEM_LON_MAX=65 \
#     docker compose up -d --build dem-assets
#
# Or skip the bake entirely (image is built but ships no DEM; analytics
# endpoints return 503 as if the bake had never run):
#   DEM_ENABLED=0 docker compose build dem-assets
#
# Build context is the repo root (compose passes `context: .`) so the
# fetcher script under scripts/ is reachable.

# ---------------------------------------------------------------- fetcher
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.9.2 AS fetcher

ARG DEM_ENABLED=1
ARG DEM_CONCURRENCY=8
ARG DEM_LAT_MIN=-90
ARG DEM_LAT_MAX=90
ARG DEM_LON_MIN=-180
ARG DEM_LON_MAX=180

WORKDIR /build
COPY scripts/build_offline_dem.py /build/build_offline_dem.py

# BuildKit cache mount keeps the multi-hundred-GB tile fetch warm across
# rebuilds. After the fetch we copy out to /work so the next stage can
# COPY --from=fetcher without pulling the cache mount into the image.
RUN --mount=type=cache,target=/cache/dem \
    mkdir -p /work/dem \
 && if [ "${DEM_ENABLED}" = "1" ]; then \
        python3 /build/build_offline_dem.py \
            --out /cache/dem \
            --concurrency "${DEM_CONCURRENCY}" \
            --lat-min "${DEM_LAT_MIN}" --lat-max "${DEM_LAT_MAX}" \
            --lon-min "${DEM_LON_MIN}" --lon-max "${DEM_LON_MAX}" \
        && cp -an /cache/dem/. /work/dem/ \
    ; else \
        echo "DEM_ENABLED=0 — skipping GLO-30 bake (slim image)" \
        && echo "skipped" > /work/dem/MANIFEST.sha256 \
    ; fi

# ------------------------------------------------------------------ final
FROM alpine:3.20

RUN apk add --no-cache rsync bash

# Baked tile tree + VRT + MANIFEST.sha256. The entrypoint rsyncs from here
# onto the dem_data volume on first start.
COPY --from=fetcher /work/dem/ /opt/baked-dem/

COPY dem-assets/scripts/entrypoint.sh /usr/local/bin/dem-assets-entrypoint
RUN chmod +x /usr/local/bin/dem-assets-entrypoint

RUN test -f /opt/baked-dem/MANIFEST.sha256

ENTRYPOINT ["/usr/local/bin/dem-assets-entrypoint"]
