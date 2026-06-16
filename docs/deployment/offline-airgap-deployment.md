# Offline / Air-Gap Deployment

## Purpose

The build-once / load-and-go runbook for disconnected sites. AI model weights are baked into the inference image; fonts, reference-corpora, and calibration are baked into the assets image. Geo-asset data (basemap tiles, terrain tiles, DEM, OSRM routing) is fetched by runtime baker containers and stored in `./assets` on the host — the deployed stack only reads `./assets`, never fetches.

## Connected host (full prep)

```bash
# 1. Detect host GPU + driver and write build settings to .env
python scripts/configure_host.py

# 2. Set HF_TOKEN in .env only for the connected build host when gated weights are needed.
#    Never commit real tokens to .env.example or docs.
echo "HF_TOKEN=<huggingface-token>" >> .env

# 3. Session secret + admin password + database passwords (all fail-closed).
#    Set DB passwords BEFORE the first `up` so the neo4j_data / pg_data volumes
#    initialize with them — they only apply on first boot (see
#    decisions/why-env-driven-db-credentials-2026-06-16.md for in-place rotation).
echo "SESSION_SECRET=$(openssl rand -hex 32)" >> .env
echo "ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')" >> .env
echo "NEO4J_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')" >> .env
echo "POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')" >> .env

# 4. Set region for OSRM (pick a Geofabrik extract that fits your host RAM;
#    full-planet/all-Asia OOMs on 30 GB hosts — see osrm-planet-bake.md)
echo "PLANET_PBF_URL=https://download.geofabrik.de/europe/germany-latest.osm.pbf" >> .env

# 5. Reclaim orphaned BuildKit cache (one-time; recovers ~1.1 TB on a busy host)
docker buildx prune -f

# 6. Fetch geo-assets into ./assets  (~hours; resumable if interrupted)
docker compose --profile bake up osrm-baker dem-baker basemap-baker terrain-baker

# 7. Build application images and start the stack (fast — no downloads)
docker compose up -d --build

# 8. Save images for transport
docker save $(docker compose config --images) | gzip > sentinel-bundle.tar.gz
```

Steps 6 and 7 are independent of each other and can be run in any order; the baker data in `./assets` persists across image rebuilds.

## Disconnected host (load + run)

```bash
# Transfer sentinel-bundle.tar.gz and the ./assets folder to the air-gap host, then:
gunzip -c sentinel-bundle.tar.gz | docker load
docker compose up -d
```

`./assets` is read-only at runtime — the stack never attempts outbound network access. Remote HTTP(S) imagery ingest is disabled by default (`ALLOW_REMOTE_IMAGERY_URLS=0`) so disconnected deployments process staged local files under `/data/imagery`.

## Rebuilding the inference image offline

`docker compose build` normally downloads ~16 GB of model weights from Hugging Face — impossible on a disconnected host, and re-incurred whenever the BuildKit cache is pruned (step 5 above). To rebuild **without** re-fetching, stage a previously-baked `/models` tree into `./model-cache`; the build seeds `/models` from it and runs the bake HF-offline:

```bash
# One-time: copy the baked models out of a working inference image/container
docker cp osint-inference-sam3-1:/models/. ./model-cache/

# Rebuild — the model_cache build context seeds /models locally, no downloads.
# Point elsewhere with SAM3_MODEL_CACHE_DIR=/path/to/cache.
docker compose build inference-sam3 && docker compose up -d inference-sam3
```

`./model-cache` is gitignored (tracked only via `.gitkeep`) so the 16 GB never enters source control but the build-context path always exists. An empty `./model-cache` (fresh clone / connected build host) falls through to the normal online bake. Seed step: [inference-gpu-dockerfile.md](inference-gpu-dockerfile.md); context wiring: `docker-compose.yml` `inference-sam3.build.additional_contexts`.

**Dependency reproducibility:** `backend/requirements.txt` and `inference-sam3/requirements.txt`
pin every direct dependency with `==` to a known-good `pip freeze` set, and `frontend/`
commits `package-lock.json` (installed via `npm ci`), so a connected-host rebuild resolves
the same versions rather than drifting. PyTorch is GPU-profile-injected (see
[python-requirements.md](../inference/python-requirements.md)). The model weights, not the
wheels, are what `./model-cache` seeds — an offline pip rebuild still needs the pinned wheels
reachable, so rebuild on a connected host (the supported path) unless a local wheelhouse is staged.

## What's baked in

**Application images (baked at `docker compose build`):**

- All Hugging Face model weights listed in [inference/model-manifest.md](../inference/model-manifest.md)
- IBM Plex webfonts + SIL OFL 1.1 license bundle
- Natural Earth country polygons (border GeoJSON)
- Reference-corpora chips (see [operations/reference-corpora-bake.md](../operations/reference-corpora-bake.md))
- Per-detector calibration temperatures (see [operations/calibration-shipping.md](../operations/calibration-shipping.md))

**`./assets` folder (fetched by baker containers before `docker compose build`):**

- Carto Dark Matter basemap tiles (z=0..14), ~13 GB — see [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- OpenTopoMap terrain tiles (z=0..14), ~22 GB
- Copernicus GLO-30 DEM tiles + VRT (~150 GB worldwide) — see [dem-glo30-bake.md](dem-glo30-bake.md)
- OSRM MLD dataset (~150-200 GB planet, less for regional) — see [osrm-planet-bake.md](osrm-planet-bake.md)

## Partial deployments (missing geo-assets)

The stack starts and serves honestly even when parts of `./assets` are absent:

| Missing data | Effect |
|---|---|
| `./assets/osrm` empty | `osrm` service unhealthy; `/api/analytics/routes` returns 503 |
| `./assets/dem` empty | `/api/analytics/viewshed` and `/api/analytics/los` return 503 |
| `./assets/static/basemap` empty | Basemap overlay absent in UI; map still works with imagery |
| `./assets/static/terrain` empty | Terrain overlay absent in UI; analytics still work |

## Runtime DNS verification

After `docker compose up`, verify zero outbound traffic with `tcpdump` and `docker network create --internal`. All upstream images pinned to specific digests for byte-for-byte reproducibility.

## Dev override

The offline image bakes weights into the container. For day-to-day inference iteration, layer a `docker-compose.dev.yml` with a writable `sam3_models` volume → restores the "first run downloads, subsequent runs reuse" loop.

## Cross-references

- [scripts/configure-host-gpu.md](../scripts/configure-host-gpu.md)
- [scripts/build-offline-basemap.md](../scripts/build-offline-basemap.md)
- [scripts/build-offline-terrain.md](../scripts/build-offline-terrain.md)
- [deployment/osrm-planet-bake.md](osrm-planet-bake.md)
- [deployment/dem-glo30-bake.md](dem-glo30-bake.md)
- [inference/model-manifest.md](../inference/model-manifest.md)
- [environment-variables-reference.md](environment-variables-reference.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
- [decisions/why-runtime-bakers-into-assets.md](../decisions/why-runtime-bakers-into-assets.md)
