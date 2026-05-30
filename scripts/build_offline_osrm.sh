#!/bin/bash
# Pre-build a planet OSRM dataset for air-gap deployments.
#
# Run on a connected host once via the `osrm-baker` Compose profile:
#
#   docker compose --profile bake-osrm up --build osrm-baker
#
# Steps:
#   1. Download planet-latest.osm.pbf (~80 GB) from PLANET_PBF_URL into /data
#      (or skip if /data/planet.osm.pbf already exists).
#   2. osrm-extract  -p /opt/car.lua  /data/planet.osm.pbf
#   3. osrm-partition /data/planet.osrm
#   4. osrm-customize /data/planet.osrm
#
# The result lives on the `osrm_data` named volume (~150-200 GB). The `osrm`
# service then mounts it read-only and runs `osrm-routed --algorithm mld`.
#
# Idempotent: each step skips if its expected outputs already exist. Delete
# the targets to force a rebuild.

set -euo pipefail

DATA_DIR="${DATA_DIR:-/data}"
PLANET_PBF="${DATA_DIR}/planet.osm.pbf"
PLANET_OSRM="${DATA_DIR}/planet.osrm"
PROFILE="${PROFILE:-/opt/car.lua}"
PLANET_PBF_URL="${PLANET_PBF_URL:-https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf}"

log() { echo "[osrm-baker $(date -u +%H:%M:%SZ)] $*"; }

if [ ! -f "${PROFILE}" ]; then
    echo "FATAL: OSRM profile not found at ${PROFILE}" >&2
    exit 2
fi

# osrm-backend v6 is alpine-based; install curl via apk if missing. Earlier
# osrm-backend tags were debian-based — fall back to apt-get for those.
if ! command -v curl >/dev/null 2>&1; then
    log "installing curl"
    if command -v apk >/dev/null 2>&1; then
        apk add --no-cache curl ca-certificates
    elif command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq && apt-get install -y --no-install-recommends curl ca-certificates
    else
        echo "FATAL: no curl and no package manager (apk/apt-get) to install it" >&2
        exit 2
    fi
fi

# 1. Fetch planet PBF.
if [ -s "${PLANET_PBF}" ]; then
    log "planet PBF already present at ${PLANET_PBF} ($(du -h "${PLANET_PBF}" | cut -f1)); skipping download"
else
    log "downloading planet PBF from ${PLANET_PBF_URL}"
    # --continue lets a partial download resume; --retry handles transient
    # 5xx on the OSM mirror.
    curl --fail --location --continue-at - --retry 5 --retry-delay 30 \
         --output "${PLANET_PBF}.partial" \
         "${PLANET_PBF_URL}"
    mv "${PLANET_PBF}.partial" "${PLANET_PBF}"
    log "downloaded $(du -h "${PLANET_PBF}" | cut -f1)"
fi

# 2. osrm-extract — produces planet.osrm and a swarm of sidecar files.
if [ -s "${PLANET_OSRM}" ] && [ -s "${PLANET_OSRM}.edges" ]; then
    log "${PLANET_OSRM} already extracted; skipping osrm-extract"
else
    log "osrm-extract — this is the long step (~3-6 h on a fast CPU)"
    osrm-extract -p "${PROFILE}" "${PLANET_PBF}"
fi

# 3. osrm-partition.
if [ -s "${PLANET_OSRM}.partition" ]; then
    log "partition already built; skipping osrm-partition"
else
    log "osrm-partition"
    osrm-partition "${PLANET_OSRM}"
fi

# 4. osrm-customize.
if [ -s "${PLANET_OSRM}.cell_metrics" ]; then
    log "customize already built; skipping osrm-customize"
else
    log "osrm-customize"
    osrm-customize "${PLANET_OSRM}"
fi

log "bake complete; contents of ${DATA_DIR}:"
du -sh "${DATA_DIR}"/* 2>/dev/null || true

# Emit MANIFEST.sha256 — single digest over (sorted artifact name, size) pairs.
# Written for air-gap integrity and inspection; operators can compare this
# against the manifest on the air-gap copy to confirm the dataset was
# transferred intact. We hash names + sizes (not contents) to keep this
# step fast on the multi-hundred-GB output. Avoid GNU-only `find -printf`
# since this script runs inside the alpine-based osrm-backend image.
log "writing MANIFEST.sha256"
(
    cd "${DATA_DIR}"
    for f in planet.osrm* *.osm.pbf; do
        [ -f "$f" ] || continue
        printf '%s\t%s\n' "$f" "$(stat -c '%s' "$f" 2>/dev/null || wc -c <"$f")"
    done \
        | sort \
        | sha256sum \
        | awk '{print $1}' > MANIFEST.sha256
)
log "MANIFEST.sha256: $(cat ${DATA_DIR}/MANIFEST.sha256)"
