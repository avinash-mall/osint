#!/usr/bin/env bash
# Wrap scripts/fetch_reference_datasets.py for the assets image build.
#
# Args:
#   $1  out_root        — where to write the chip tree + MANIFEST.sha256
#   $2  dropin_root     — bind-mounted ./reference-corpora-input/ (may be empty)
#   $3  manifests_root  — bake-time copy of scripts/manifests/
#
# Behavior:
#   - Reads HF_TOKEN from $REFERENCE_CORPORA_HF_TOKEN (so the build arg
#     doesn't appear in the final image's environ; arg-secret is fine for
#     personal-use builds).
#   - Caps chips per class via $REFERENCE_MAX_CHIPS_PER_CLASS (default 50).
#   - Exits non-zero ONLY when every adapter fails (chips_total=0). Single
#     adapter errors are warnings — by design, since most adapters are
#     optional / drop-in.

set -euo pipefail

out_root=${1:?out_root required}
dropin_root=${2:?dropin_root required}
manifests_root=${3:?manifests_root required}

max_chips=${REFERENCE_MAX_CHIPS_PER_CLASS:-50}

# Surface HF_TOKEN to the fetcher without putting it in the image environ.
if [ -n "${REFERENCE_CORPORA_HF_TOKEN:-}" ]; then
    export HF_TOKEN="${REFERENCE_CORPORA_HF_TOKEN}"
fi

mkdir -p "${out_root}"

echo "[reference-corpora] out=${out_root}  dropin=${dropin_root}  manifests=${manifests_root}"
echo "[reference-corpora] max-chips-per-class=${max_chips}  hf_token_present=$([ -n "${HF_TOKEN:-}" ] && echo yes || echo no)"

python /build/fetch_reference_datasets.py \
    --out "${out_root}" \
    --dropin "${dropin_root}" \
    --manifests "${manifests_root}" \
    --max-chips-per-class "${max_chips}" \
    --verbose

echo "[reference-corpora] MANIFEST.sha256: $(cat ${out_root}/MANIFEST.sha256)"
