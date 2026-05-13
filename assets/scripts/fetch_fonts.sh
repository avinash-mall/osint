#!/usr/bin/env bash
# Fetch IBM Plex Sans + Mono woff2 files (latin subset) from the upstream
# IBM/plex GitHub release on the connected build host and stage them under
# assets/static/fonts/ for the assets Docker image to consume.
#
# IBM Plex is licensed under SIL Open Font License 1.1 — self-hosting and
# redistribution are explicitly permitted, provided the OFL.txt is
# bundled alongside the font files (see assets/static/LICENSE.txt).
#
# Re-run safely: skips files that already exist with non-zero size.
set -euo pipefail

# Pin to a tagged release rather than `main` so air-gap rebuilds are
# bit-for-bit reproducible. Bump the tag here when refreshing fonts.
PLEX_VERSION="${PLEX_VERSION:-@ibm/plex-sans@1.1.0}"
PLEX_MONO_VERSION="${PLEX_MONO_VERSION:-@ibm/plex-mono@1.1.0}"

REPO_BASE="https://raw.githubusercontent.com/IBM/plex/master/packages"

# Resolve script-relative paths so the operator can run this from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FONT_DIR="${ASSETS_DIR}/static/fonts"
LICENSE_PATH="${ASSETS_DIR}/static/LICENSE.txt"

mkdir -p "${FONT_DIR}"

# IBM Plex Sans + Mono weights actually referenced by frontend/src/index.css.
declare -A SANS_WEIGHTS=(
    [400]="IBMPlexSans-Regular-Latin1.woff2"
    [500]="IBMPlexSans-Medium-Latin1.woff2"
    [600]="IBMPlexSans-SemiBold-Latin1.woff2"
    [700]="IBMPlexSans-Bold-Latin1.woff2"
)
declare -A MONO_WEIGHTS=(
    [400]="IBMPlexMono-Regular-Latin1.woff2"
    [500]="IBMPlexMono-Medium-Latin1.woff2"
    [600]="IBMPlexMono-SemiBold-Latin1.woff2"
)

fetch_one() {
    local family="$1"          # sans or mono
    local weight="$2"          # 400, 500, ...
    local upstream_name="$3"   # IBMPlexSans-Regular-Latin1.woff2
    local out="${FONT_DIR}/ibm-plex-${family}-${weight}.woff2"

    if [[ -s "${out}" ]]; then
        echo "[fonts] skip ${out} (already present)"
        return 0
    fi

    local url="${REPO_BASE}/plex-${family}/fonts/complete/woff2/latin1/${upstream_name}"
    echo "[fonts] fetch ${url}"
    curl -fsSL --retry 3 --retry-delay 2 -o "${out}.partial" "${url}"
    mv "${out}.partial" "${out}"
}

for w in "${!SANS_WEIGHTS[@]}"; do fetch_one sans "${w}" "${SANS_WEIGHTS[$w]}"; done
for w in "${!MONO_WEIGHTS[@]}"; do fetch_one mono "${w}" "${MONO_WEIGHTS[$w]}"; done

# Bundle the SIL OFL 1.1 license — required by the IBM Plex distribution
# terms whenever the fonts are redistributed.
if [[ ! -s "${LICENSE_PATH}" ]]; then
    echo "[fonts] fetch OFL.txt"
    curl -fsSL --retry 3 --retry-delay 2 \
        -o "${LICENSE_PATH}" \
        "https://raw.githubusercontent.com/IBM/plex/master/packages/plex-sans/OFL.txt"
fi

# Emit a build-metadata stub the assets image surfaces at /.build-metadata.json.
META_PATH="${ASSETS_DIR}/static/.build-metadata.json"
python3 - "$META_PATH" "${PLEX_VERSION}" "${PLEX_MONO_VERSION}" "${FONT_DIR}" <<'PY'
import json, os, sys
from datetime import datetime, timezone
meta_path, plex_sans, plex_mono, font_dir = sys.argv[1:]
fonts = sorted(f for f in os.listdir(font_dir) if f.endswith(".woff2"))
json.dump({
    "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "plex_sans_version": plex_sans,
    "plex_mono_version": plex_mono,
    "fonts": fonts,
}, open(meta_path, "w"), indent=2)
PY

echo "[fonts] done"
