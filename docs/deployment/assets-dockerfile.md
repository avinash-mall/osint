# `assets/Dockerfile` — Air-Gap Assets Image

**Path:** [assets/Dockerfile](../../assets/Dockerfile)
**Lines:** ~129
**Depends on:** `python:3.11-slim` (fetcher stage); `nginx:1.27.3-alpine` (final stage); `assets/scripts/entrypoint.sh`; `assets/nginx.conf`; `scripts/fetch_reference_datasets.py`; `assets/scripts/build_reference_corpora.sh`; `assets/scripts/fetch_fonts.sh`; env `REFERENCE_CORPORA_ENABLED`, `REFERENCE_CORPORA_HF_TOKEN`, `REFERENCE_MAX_CHIPS_PER_CLASS`

## Purpose

Builds `sentinel-assets:offline` — the nginx static-file server that ships IBM Plex fonts, reference-corpora chips, and per-detector calibration temperatures into the air-gap stack. Basemap and terrain tile pyramids are **not** baked here; they are served via read-only bind mounts from `./assets/static/{basemap,terrain}` populated by the `bake`-profile baker containers.

## Why this design

Fonts and calibration are small (~50 MB total) and always needed — baking them in keeps the image self-contained. Reference-corpora chips (~5-10 GB, HF-sourced) use a BuildKit cache mount that survives repeated builds without re-downloading from Hugging Face. Basemap/terrain tiles (~35 GB combined) were removed from this image in the 2026-05-30 refactor because BuildKit cache mount writes roll back on cancellation and the data was accumulating as orphaned unreusable cache (~1.1 TB). They are now fetched by runtime baker containers that write directly to the host. See [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md).

The entrypoint rsyncs reference-corpora and calibration from their image-internal staging paths (`/opt/baked-reference-chips/`, `/opt/baked-calibration/`) onto named volumes on every container start, digest-gated by `MANIFEST.sha256`. Basemap/terrain bind mounts bypass this mechanism entirely.

The healthcheck probes `/healthz` and `/calibration/model_temperatures.json` only — it does not probe tile paths, so the `assets` service becomes healthy before any baker has run. The stack can start with empty basemap/terrain; those overlays are simply absent in the UI.

## Key symbols

- [`assets/scripts/entrypoint.sh`](../../assets/scripts/entrypoint.sh#L1) — rsync-on-mismatch for reference-corpora and calibration volumes; starts nginx.
- [`assets/nginx.conf`](../../assets/nginx.conf#L1) — static file serving; passes `/basemap` and `/terrain` to the bind-mounted directories.
- `/opt/baked-reference-chips/` — image-internal staging path for reference chips (rsync source).
- `/opt/baked-calibration/` — image-internal staging path for calibration JSON (rsync source).

## Inputs / Outputs

- **Build args:** `REFERENCE_CORPORA_ENABLED` (default `1`), `REFERENCE_CORPORA_HF_TOKEN`, `REFERENCE_MAX_CHIPS_PER_CLASS` (default `50`), `PLEX_VERSION`, `PLEX_MONO_VERSION`.
- **Runtime bind mounts:** `./assets/static/basemap:/usr/share/nginx/html/basemap:ro`, `./assets/static/terrain:/usr/share/nginx/html/terrain:ro`.
- **Named volumes at runtime:** `reference_corpora_data` (mounts at `/usr/share/nginx/html/reference-chips`), `calibration_data` (mounts at `/data/calibration`).
- **Exposed:** port 80; accessed internally by nginx gateway at `/basemap`, `/terrain`, `/fonts`, `/reference-chips`, `/calibration`.

## Failure modes

- `REFERENCE_CORPORA_ENABLED=0`: reference-chips MANIFEST set to `skipped`; auto-seed in backend lifespan logs a warning and skips.
- Missing `./assets/static/basemap` bind mount: nginx returns 404 for tile requests; map shows no basemap overlay; analytics panel shows overlay unavailable.
- Missing calibration JSON: healthcheck fails; stack does not start.

## Cross-references

- [operations/reference-corpora-bake.md](../operations/reference-corpora-bake.md)
- [operations/calibration-shipping.md](../operations/calibration-shipping.md)
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
- [decisions/why-bake-reference-corpora-into-assets.md](../decisions/why-bake-reference-corpora-into-assets.md)
- [deployment/nginx-gateway-and-tile-cache.md](nginx-gateway-and-tile-cache.md)
- [deployment/offline-airgap-deployment.md](offline-airgap-deployment.md)
