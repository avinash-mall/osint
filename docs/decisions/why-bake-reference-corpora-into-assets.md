# Why reference corpora are baked into the `assets` image

**Decision:** The reference-imagery corpora (DOTA, Wikimedia, drop-in trees for xView/DIOR/HRSC2016/ShipRSImageNet) are downloaded + staged inside the `assets` docker image at build time, distributed alongside the basemap + terrain tile pyramids. A new named volume `reference_corpora_data` is RW-mounted into `assets` and RO-mounted into `backend` + `worker`. The assets entrypoint rsyncs the baked content into the volume on startup whenever the volume's `MANIFEST.sha256` lags the image's.

**Date:** 2026-05-27.

## Context

The Reference Embedding DB ships with empty tables. Without baked corpora, operators must manually fetch every dataset family and run the per-dataset recipes. We needed a way to "just work" out of the box, while keeping CLAUDE.md rule 8 (no runtime network access) intact.

Three layering options were considered:

1. **Sibling `reference-corpora` image** — a build-only image just for the chip tree, COPY --from'd into backend.
2. **Extend `assets`** — fold the corpora bake into the existing static-content image (this decision).
3. **Bake into `backend`** — multi-stage backend Dockerfile downloads corpora during build.

## Why extend `assets`

- **assets is already the "all baked static content" image.** It handles basemap tile pyramids (~3 GB), terrain pyramids, and webfonts. Adding reference chips matches its purpose: the canonical airgap-shippable artifact.
- **Single distribution unit.** Operators distributing the air-gapped bundle ship one assets image, not two. The basemap + corpora share the same tagged release.
- **Shared cache discipline.** assets already uses BuildKit `--mount=type=cache` for multi-GB downloads. The corpora fetcher slots into the same pattern (`--mount=type=cache,target=/cache/reference-corpora`).
- **Decoupled from backend rebuilds.** Code changes to backend/worker do NOT re-run the multi-GB corpora fetch. Only `docker compose build assets` re-runs it. The corpora image is rebuilt on a slower cadence than backend code.
- **No new service.** Avoided adding a separate `reference-corpora` service — one less compose entry, one less image to coordinate.

## Trade-off accepted

- **assets is now larger.** With all adapters firing, the assets image can grow by 5–10 GB. Acceptable for personal-use deployments where the user opted in to "download all" corpora. The `REFERENCE_CORPORA_ENABLED=0` build arg produces a slim variant for smoke builds.
- **Chip access is via shared volume, not HTTP.** The bake reads chips from a filesystem path, not from the assets nginx. We mount the same `reference_corpora_data` volume RO into `backend` + `worker`. Slightly more compose plumbing than HTTP fetch, but file I/O is much faster for the bake's tight loop.

## How the rsync-on-startup pattern works

The Dockerfile splits the image into two locations:
- `/usr/share/nginx/html/{basemap,terrain,fonts}/` — directly served, mounted-over by nothing.
- `/opt/baked-reference-chips/` — the corpora tree at image-bake time, NOT in the served path.

The `assets` service in compose mounts the `reference_corpora_data` volume at `/usr/share/nginx/html/reference-chips/`. The entrypoint compares the volume's `MANIFEST.sha256` against `/opt/baked-reference-chips/MANIFEST.sha256`:

| Volume state | Image state | Action |
|---|---|---|
| Empty (fresh volume) | Has digest | rsync image → volume |
| Has digest X | Has digest X | skip — volume is current |
| Has digest X | Has digest Y (newer build) | rsync image → volume (`--delete-after`) |

This solves the case the original e2e verify flagged: `docker compose down -v && up -d` with a new image. The new chips replace the old volume content automatically.

## How to apply

- Build: `docker compose build assets` (HF_TOKEN in `.env` for gated datasets).
- Opt out: `REFERENCE_CORPORA_ENABLED=0 docker compose build assets` produces a slim variant.
- Restricted-access datasets (xView/DIOR/HRSC2016/ShipRSImageNet): drop tarballs under `./reference-corpora-input/<dataset>/`. The fetcher's `_fetch_dropin_only` adapter detects and processes them. Skipped silently when absent.
- See [reference-corpora-bake.md](../operations/reference-corpora-bake.md) for the operator runbook.

## Cross-references

- [why-celery-task-from-lifespan.md](why-celery-task-from-lifespan.md) — paired decision for the auto-seed trigger.
- [why-pgvector-for-reference-db.md](why-pgvector-for-reference-db.md) — schema this bake writes into.
- Build script: [`scripts/fetch_reference_datasets.py`](../../scripts/fetch_reference_datasets.py).
- Bake target: [`backend/scripts/bake_reference_index.py`](../../backend/scripts/bake_reference_index.py) `run()`.
- Volume + mount wiring: [`docker-compose.yml`](../../docker-compose.yml) `assets`, `backend`, `worker` services.
