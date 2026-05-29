# syntax=docker/dockerfile:1.7
#
# Air-gap OSRM bake image. Downloads the planet OSM PBF (~80 GB) and runs
# the standard `osrm-extract` / `osrm-partition` / `osrm-customize`
# pipeline against the unmodified `car.lua` profile bundled with
# `ghcr.io/project-osrm/osrm-backend:v6.0.0`, leaving ~150-200 GB of MLD
# artifacts inside the image. On container start the entrypoint rsyncs
# them onto the `osrm_data` named volume; the runtime `osrm` service then
# mounts that volume RO and serves /route/v1/* on port 5000.
#
# BuildKit cache mount (/cache/osrm) persists the planet PBF + the
# extract/partition/customize outputs across rebuilds — only the first
# build pays the multi-day wallclock cost.
#
# Override the PBF source for a faster regional smoke build (Geofabrik
# regional extracts are much smaller):
#   PLANET_PBF_URL=https://download.geofabrik.de/asia/gcc-states-latest.osm.pbf \
#     docker compose up -d --build osrm-assets
#
# Or skip the bake entirely:
#   OSRM_ENABLED=0 docker compose build osrm-assets
#
# Build context is the repo root (compose passes `context: .`).

# ---------------------------------------------------------------- fetcher
FROM ghcr.io/project-osrm/osrm-backend:v6.0.0 AS fetcher

ARG OSRM_ENABLED=1
ARG PLANET_PBF_URL=https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf

# osrm-backend v6 is alpine-based; the upstream image does not ship curl.
# The bake script also has a defensive install fallback, but installing
# here keeps the build deterministic and gives the cache mount layer
# something cheap to invalidate on.
RUN apk add --no-cache curl ca-certificates bash

WORKDIR /build
COPY scripts/build_offline_osrm.sh /build/build_offline_osrm.sh
RUN chmod +x /build/build_offline_osrm.sh

ENV PROFILE=/opt/car.lua
ENV DATA_DIR=/cache/osrm

# BuildKit cache mount holds the multi-hundred-GB PBF + MLD outputs across
# rebuilds. Copy artifacts (NOT the PBF) into /work for the final stage.
# The PBF is huge and not needed at runtime — only the .osrm* artifacts.
RUN --mount=type=cache,target=/cache/osrm \
    mkdir -p /work/osrm \
 && if [ "${OSRM_ENABLED}" = "1" ]; then \
        PLANET_PBF_URL="${PLANET_PBF_URL}" \
        PROFILE="${PROFILE}" \
        DATA_DIR=/cache/osrm \
        /build/build_offline_osrm.sh \
        && find /cache/osrm -maxdepth 1 -type f -name 'planet.osrm*' -exec cp -an {} /work/osrm/ \; \
        && cp -an /cache/osrm/MANIFEST.sha256 /work/osrm/MANIFEST.sha256 \
    ; else \
        echo "OSRM_ENABLED=0 — skipping planet bake (slim image)" \
        && echo "skipped" > /work/osrm/MANIFEST.sha256 \
    ; fi

# ------------------------------------------------------------------ final
FROM alpine:3.20

RUN apk add --no-cache rsync bash

COPY --from=fetcher /work/osrm/ /opt/baked-osrm/

COPY osrm-assets/scripts/entrypoint.sh /usr/local/bin/osrm-assets-entrypoint
RUN chmod +x /usr/local/bin/osrm-assets-entrypoint

RUN test -f /opt/baked-osrm/MANIFEST.sha256

ENTRYPOINT ["/usr/local/bin/osrm-assets-entrypoint"]
