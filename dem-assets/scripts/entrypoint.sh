#!/usr/bin/env bash
# Sentinel dem-assets entrypoint.
#
# The image bakes the worldwide Copernicus GLO-30 DEM (tiles + VRT) into
# /opt/baked-dem/ at build time. On container start we rsync that content
# onto the dem_data named volume mounted at /data/dem, when:
#   - the mount is empty (fresh volume), OR
#   - the volume's MANIFEST.sha256 differs from the image's
#     (image got rebuilt with new tiles; volume is stale)
#
# If DEM_ENABLED=0 was set at build time, /opt/baked-dem/MANIFEST.sha256
# contains the literal string "skipped" and we exit 0 without rsyncing —
# the runtime backend will then see no DEM and serve 503 for viewshed/LOS
# until a real bake is built.
#
# This container is meant to run as a one-shot init container; downstream
# services wait via `depends_on: { dem-assets: { condition:
# service_completed_successfully } }`.

set -euo pipefail

readonly BAKED=/opt/baked-dem
readonly MOUNTED=/data/dem

if [ ! -d "${BAKED}" ]; then
    echo "[dem-assets] FATAL: no baked DEM at ${BAKED}" >&2
    exit 2
fi

if [ ! -f "${BAKED}/MANIFEST.sha256" ]; then
    echo "[dem-assets] FATAL: ${BAKED}/MANIFEST.sha256 missing" >&2
    exit 2
fi

baked_digest=$(head -n1 "${BAKED}/MANIFEST.sha256")

if [ "${baked_digest}" = "skipped" ]; then
    echo "[dem-assets] DEM_ENABLED=0 was set at build time — no data to seed"
    exit 0
fi

mkdir -p "${MOUNTED}"
mounted_digest=$(head -n1 "${MOUNTED}/MANIFEST.sha256" 2>/dev/null || true)

if [ "${baked_digest}" = "${mounted_digest}" ] && [ -n "${baked_digest}" ]; then
    echo "[dem-assets] volume matches image (digest=${baked_digest:0:12}) — skip rsync"
    exit 0
fi

echo "[dem-assets] rsync ${BAKED}/ → ${MOUNTED}/ (image=${baked_digest:0:12} volume=${mounted_digest:0:12:-empty})"
# --delete-after prunes content from previous bakes that no longer exists
# in the image, while keeping the directory readable mid-rsync.
rsync -a --delete --delete-after "${BAKED}/" "${MOUNTED}/"
echo "[dem-assets] rsync done"
