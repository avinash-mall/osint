# syntax=docker/dockerfile:1.7
#
# Air-gap assets image: an nginx server for the basemap + terrain tile
# pyramids, IBM Plex webfonts, reference-corpora chips, and per-detector
# calibration. Fonts/reference/calibration are baked in at build time;
# the basemap + terrain pyramids are NO LONGER baked here — they are
# fetched by the runtime basemap-baker / terrain-baker services (Compose
# `bake` profile) into ./assets/static/{basemap,terrain} and bind-mounted
# read-only into this container's nginx root. See
# docs/decisions/why-runtime-bakers-into-assets.md.
#
# Build context is the repo root (compose passes `context: .`) so the
# fetcher scripts under scripts/ are reachable.

# ---------------------------------------------------------------- fetcher
FROM python:3.11-slim AS fetcher

ARG PLEX_VERSION=@ibm/plex-sans@1.1.0
ARG PLEX_MONO_VERSION=@ibm/plex-mono@1.1.0

# Reference-corpora bake — set REFERENCE_CORPORA_ENABLED=0 to skip entirely
# (faster smoke builds). HF_TOKEN unlocks the gated dataset adapters; absent
# means those adapters log + skip, the build still succeeds.
ARG REFERENCE_CORPORA_ENABLED=1
ARG REFERENCE_CORPORA_HF_TOKEN
ARG REFERENCE_MAX_CHIPS_PER_CLASS=50

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates bash \
 && rm -rf /var/lib/apt/lists/*

# Reference-corpora Python deps. Installed only when enabled to keep the
# default smoke build slim.
RUN if [ "${REFERENCE_CORPORA_ENABLED}" = "1" ]; then \
        pip install --no-cache-dir --quiet \
            "huggingface_hub>=0.24,<1" \
            "Pillow>=10" \
    ; fi

WORKDIR /build
COPY scripts/fetch_reference_datasets.py     /build/fetch_reference_datasets.py
COPY scripts/manifests/                      /build/manifests/
COPY backend/scripts/stage_dota_chips.py     /build/backend-scripts/stage_dota_chips.py
COPY assets/scripts/fetch_fonts.sh           /build/fetch_fonts.sh
COPY assets/scripts/build_reference_corpora.sh /build/build_reference_corpora.sh
COPY assets/static/                          /seed/

# Mirror the repo layout fetch_fonts.sh assumes (it resolves paths
# relative to its own location: ASSETS_DIR=$SCRIPT_DIR/..).
RUN mkdir -p /work/static \
             /stage/assets/scripts \
             /stage/assets/static/fonts \
 && cp /build/fetch_fonts.sh /stage/assets/scripts/fetch_fonts.sh \
 && chmod +x /stage/assets/scripts/fetch_fonts.sh \
 && if [ -d /seed/fonts ]; then cp -an /seed/fonts/. /stage/assets/static/fonts/ || true; fi

# Fonts — tiny payload, no cache mount needed.
RUN PLEX_VERSION="${PLEX_VERSION}" PLEX_MONO_VERSION="${PLEX_MONO_VERSION}" \
        bash /stage/assets/scripts/fetch_fonts.sh \
 && cp -an /stage/assets/static/. /work/static/

# Reference-corpora bake. Optional drop-in mount for restricted-access
# tarballs (xView/DIOR/HRSC2016/ShipRSImageNet); BuildKit cache mount keeps
# multi-GB HF pulls fast across rebuilds.
RUN --mount=type=cache,target=/cache/reference-corpora \
    --mount=type=bind,source=reference-corpora-input,target=/dropin,rw=false \
    if [ "${REFERENCE_CORPORA_ENABLED}" = "1" ]; then \
        REFERENCE_CORPORA_HF_TOKEN="${REFERENCE_CORPORA_HF_TOKEN}" \
        REFERENCE_MAX_CHIPS_PER_CLASS="${REFERENCE_MAX_CHIPS_PER_CLASS}" \
        bash /build/build_reference_corpora.sh \
            /cache/reference-corpora \
            /dropin \
            /build/manifests \
        && mkdir -p /work/static/reference-chips \
        && cp -an /cache/reference-corpora/. /work/static/reference-chips/ \
    ; else \
        echo "REFERENCE_CORPORA_ENABLED=0 — skipping corpora bake (slim image)" \
        && mkdir -p /work/static/reference-chips \
        && echo "skipped" > /work/static/reference-chips/MANIFEST.sha256 \
    ; fi

# Per-detector calibration temperatures. The JSON + MANIFEST live under
# assets/static/calibration/ (already in /seed/ via the earlier COPY) and are
# staged into /work/static/calibration/ here so the final stage can land them
# at an un-mounted /opt/baked-calibration/ path. Operator-measured
# temperatures replace the placeholder T=1.0 values — see
# docs/operations/calibration-shipping.md.
RUN mkdir -p /work/static/calibration \
 && if [ -d /seed/calibration ]; then cp -an /seed/calibration/. /work/static/calibration/ || true; fi \
 && test -f /work/static/calibration/model_temperatures.json \
 && test -f /work/static/calibration/MANIFEST.sha256

# ------------------------------------------------------------------ final
FROM nginx:1.27.3-alpine

RUN apk add --no-cache rsync bash

COPY assets/nginx.conf            /etc/nginx/conf.d/default.conf
# Tiles + fonts go directly into the served root (these stay baked in the
# image since nothing mounts over them).
COPY --from=fetcher /work/static/fonts/   /usr/share/nginx/html/fonts/
# Reference chips go into an UN-mounted staging path. The entrypoint rsyncs
# them into /usr/share/nginx/html/reference-chips/ on first start when the
# named volume is empty or carries an older MANIFEST.sha256. This solves the
# "image rebuilt with new corpora; volume still has old set" stale-volume
# case that the e2e verify flagged.
COPY --from=fetcher /work/static/reference-chips/ /opt/baked-reference-chips/
# Calibration JSON lands at an UN-mounted staging path. Same pattern as
# reference-chips: the entrypoint rsyncs it onto the calibration_data named
# volume on startup, and the backend mounts that volume RO at
# /data/calibration/. See docs/operations/calibration-shipping.md.
COPY --from=fetcher /work/static/calibration/ /opt/baked-calibration/

COPY assets/scripts/entrypoint.sh /usr/local/bin/sentinel-assets-entrypoint
RUN chmod +x /usr/local/bin/sentinel-assets-entrypoint

RUN test -f /usr/share/nginx/html/fonts/ibm-plex-sans-400.woff2 \
 && test -f /opt/baked-reference-chips/MANIFEST.sha256 \
 && test -f /opt/baked-calibration/MANIFEST.sha256

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/healthz \
     && wget -q -O /dev/null http://127.0.0.1/calibration/model_temperatures.json \
     || exit 1

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/sentinel-assets-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]
