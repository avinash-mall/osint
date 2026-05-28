#!/usr/bin/env bash
# Sentinel osrm-assets entrypoint.
#
# The image bakes the planet OSRM MLD dataset (planet.osrm + sidecar
# files, ~150-200 GB) into /opt/baked-osrm/ at build time. On container
# start we rsync that content onto the osrm_data named volume mounted at
# /data, when:
#   - the mount is empty (fresh volume), OR
#   - the volume's MANIFEST.sha256 differs from the image's
#     (image got rebuilt with a fresher planet PBF)
#
# If OSRM_ENABLED=0 was set at build time, MANIFEST.sha256 contains
# "skipped" and we exit 0 without rsyncing — the runtime osrm-routed
# service will then fail its healthcheck (no /data/planet.osrm), backend's
# osrm_available() returns False, and /api/analytics/routes serves 503.
#
# This container is meant to run as a one-shot init container; the runtime
# `osrm` service waits via `depends_on: { osrm-assets: { condition:
# service_completed_successfully } }`.

set -euo pipefail

readonly BAKED=/opt/baked-osrm
readonly MOUNTED=/data

if [ ! -d "${BAKED}" ]; then
    echo "[osrm-assets] FATAL: no baked OSRM at ${BAKED}" >&2
    exit 2
fi

if [ ! -f "${BAKED}/MANIFEST.sha256" ]; then
    echo "[osrm-assets] FATAL: ${BAKED}/MANIFEST.sha256 missing" >&2
    exit 2
fi

baked_digest=$(head -n1 "${BAKED}/MANIFEST.sha256")

if [ "${baked_digest}" = "skipped" ]; then
    echo "[osrm-assets] OSRM_ENABLED=0 was set at build time — no data to seed"
    exit 0
fi

mkdir -p "${MOUNTED}"
mounted_digest=$(head -n1 "${MOUNTED}/MANIFEST.sha256" 2>/dev/null || true)

if [ "${baked_digest}" = "${mounted_digest}" ] && [ -n "${baked_digest}" ]; then
    echo "[osrm-assets] volume matches image (digest=${baked_digest:0:12}) — skip rsync"
    exit 0
fi

echo "[osrm-assets] rsync ${BAKED}/ → ${MOUNTED}/ (image=${baked_digest:0:12} volume=${mounted_digest:0:12:-empty})"
rsync -a --delete --delete-after "${BAKED}/" "${MOUNTED}/"
echo "[osrm-assets] rsync done"
