#!/usr/bin/env bash
# Sentinel assets entrypoint.
#
# Tiles + fonts live directly in the image at /usr/share/nginx/html/{basemap,terrain,fonts}/
# Reference-corpora chips live in a named docker volume mounted at
# /usr/share/nginx/html/reference-chips/ so the backend can read them via a
# shared-volume mount.
#
# On startup we rsync the baked content from the un-mounted /opt/baked-reference-chips/
# into the mounted /usr/share/nginx/html/reference-chips/ when:
#   - the mount is empty (fresh volume), OR
#   - the volume's MANIFEST.sha256 differs from the image's
#     (image got rebuilt with new corpora; volume is stale)
#
# This solves the "down -v && up -d still serves an old corpora set" case
# that the e2e verify flagged.

set -euo pipefail

readonly BAKED=/opt/baked-reference-chips
readonly MOUNTED=/usr/share/nginx/html/reference-chips

if [ ! -d "${BAKED}" ]; then
    echo "[entrypoint] no baked reference-chips at ${BAKED} — corpora bake was disabled at image build" >&2
elif [ ! -f "${BAKED}/MANIFEST.sha256" ]; then
    echo "[entrypoint] ${BAKED}/MANIFEST.sha256 missing — skipping rsync" >&2
else
    mkdir -p "${MOUNTED}"
    baked_digest=$(cat "${BAKED}/MANIFEST.sha256" 2>/dev/null | head -n1)
    mounted_digest=$(cat "${MOUNTED}/MANIFEST.sha256" 2>/dev/null | head -n1 || true)

    if [ "${baked_digest}" = "${mounted_digest}" ] && [ -n "${baked_digest}" ]; then
        echo "[entrypoint] reference-chips: volume matches image (digest=${baked_digest:0:12}) — skip rsync"
    else
        echo "[entrypoint] reference-chips: rsync ${BAKED}/ → ${MOUNTED}/ (image=${baked_digest:0:12} volume=${mounted_digest:0:12:-empty})"
        # Delete-after preserves directory atomicity for clients reading
        # mid-rsync. --delete prunes content from previous bakes that no
        # longer exists in the image.
        rsync -a --delete --delete-after "${BAKED}/" "${MOUNTED}/"
        echo "[entrypoint] reference-chips: rsync done"
    fi
fi

exec "$@"
