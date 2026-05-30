# syntax=docker/dockerfile:1.7
#
# OSRM baker — a runtime container (Compose `bake` profile) that downloads
# the OSM PBF and runs the extract/partition/customize pipeline directly
# into the host-mounted /data (./assets/osrm). Replaces the build-time
# osrm-assets bake: writes persist across crashes/OOM (host bind mount, no
# cache-mount rollback) and osrm-extract can use host swap.
# See docs/decisions/why-runtime-bakers-into-assets.md.
FROM ghcr.io/project-osrm/osrm-backend:v6.0.0

# osrm-backend v6 is alpine and ships neither bash nor curl; the bake
# script needs both.
RUN apk add --no-cache bash curl ca-certificates

COPY scripts/build_offline_osrm.sh /app/build_offline_osrm.sh
RUN chmod +x /app/build_offline_osrm.sh

ENV PROFILE=/opt/car.lua \
    DATA_DIR=/data

ENTRYPOINT ["bash", "/app/build_offline_osrm.sh"]
