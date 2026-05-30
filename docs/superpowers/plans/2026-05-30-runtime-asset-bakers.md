# Runtime Asset Bakers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move OSRM/DEM/basemap/terrain downloads out of build-time BuildKit cache mounts into runtime baker containers (Compose `bake` profile) that write directly to the persistent host `./assets` folder; runtime services only read `./assets`.

**Architecture:** Four one-shot baker services under `profiles: [bake]`, each bind-mounting its `./assets/<x>` target and running an existing fetch script directly into it (resumable, swap-capable, no build contention). Runtime services drop their `depends_on` on the old init containers and read `./assets` read-only. The `assets` nginx image stops baking basemap/terrain and instead serves them from a read-only bind mount; its healthcheck relaxes to `/healthz` so a tile-less stack still starts.

**Tech Stack:** Docker Compose, BuildKit, bash/python fetch scripts (`scripts/build_offline_*`), nginx, osrm-backend v6, GDAL.

**Spec:** [docs/superpowers/specs/2026-05-30-runtime-asset-bakers-design.md](../specs/2026-05-30-runtime-asset-bakers-design.md)

**Conventions to honor:**
- CLAUDE.md #8 air-gap: default `docker compose up` must perform zero downloads.
- CLAUDE.md #1 read-only dev dirs: do NOT write real data into `./assets/static/basemap`, `./assets/dem`, etc. during verification — smoke tests use a throwaway `/tmp` mount and tiny ranges.
- After each file change, update its module doc (six-section template) — batched in Task 9.

---

### Task 1: OSRM baker image

**Files:**
- Create: `bakers/osrm/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7
#
# OSRM baker — a runtime container (Compose `bake` profile) that downloads
# the OSM PBF and runs the extract/partition/customize pipeline directly
# into the host-mounted /data (./assets/osrm). Replaces the build-time
# osrm-assets bake: writes persist across crashes/OOM (host bind mount, no
# cache-mount rollback) and osrm-extract can use host swap.
# See docs/decisions/why-runtime-bakers-into-assets.md.
FROM ghcr.io/project-osrm/osrm-backend:v6.0.0

# osrm-backend v6 is alpine and ships neither bash nor curl; the bake
# script needs both.
RUN apk add --no-cache bash curl ca-certificates

COPY scripts/build_offline_osrm.sh /app/build_offline_osrm.sh
RUN chmod +x /app/build_offline_osrm.sh

ENV PROFILE=/opt/car.lua \
    DATA_DIR=/data

ENTRYPOINT ["bash", "/app/build_offline_osrm.sh"]
```

- [ ] **Step 2: Build it to verify the base image + apk layer resolve**

Run: `docker build -f bakers/osrm/Dockerfile -t sentinel-osrm-baker:offline .`
Expected: build succeeds; final image tagged. (No network fetch of map data happens at build time.)

- [ ] **Step 3: Verify the script and tools are present**

Run: `docker run --rm --entrypoint sh sentinel-osrm-baker:offline -c "command -v bash curl && head -1 /app/build_offline_osrm.sh"`
Expected: prints paths for `bash` and `curl` and the script shebang `#!/bin/bash`.

- [ ] **Step 4: Commit**

```bash
git add bakers/osrm/Dockerfile
git commit -m "feat(bakers): add osrm runtime baker image"
```

---

### Task 2: DEM baker image

**Files:**
- Create: `bakers/dem/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7
#
# DEM baker — a runtime container (Compose `bake` profile) that downloads
# Copernicus GLO-30 tiles and builds the glo30.vrt mosaic directly into the
# host-mounted /data/dem (./assets/dem). Replaces the build-time dem-assets
# bake. The fetch range + concurrency come from the compose `command:`
# (env-driven). See docs/decisions/why-runtime-bakers-into-assets.md.
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.9.2

COPY scripts/build_offline_dem.py /app/build_offline_dem.py

# No CMD: compose supplies the full python invocation with env-driven
# bounds so the same image serves any region.
```

- [ ] **Step 2: Build it**

Run: `docker build -f bakers/dem/Dockerfile -t sentinel-dem-baker:offline .`
Expected: build succeeds.

- [ ] **Step 3: Verify python + gdalbuildvrt + script present**

Run: `docker run --rm sentinel-dem-baker:offline sh -c "python3 -c 'import urllib.request' && command -v gdalbuildvrt && test -f /app/build_offline_dem.py && echo OK"`
Expected: prints a `gdalbuildvrt` path and `OK`.

- [ ] **Step 4: Commit**

```bash
git add bakers/dem/Dockerfile
git commit -m "feat(bakers): add dem runtime baker image"
```

---

### Task 3: Tiles baker image (basemap + terrain)

**Files:**
- Create: `bakers/tiles/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7
#
# Tiles baker — a runtime container (Compose `bake` profile) that fetches
# the basemap (CARTO) and terrain (OpenTopoMap) tile pyramids directly into
# the host-mounted /out (./assets/static/basemap or ./assets/static/terrain).
# One image, shared by the basemap-baker and terrain-baker services; compose
# selects which script + zoom range via `command:`. The fetch scripts use
# only the Python stdlib, so no pip install is needed.
# See docs/decisions/why-runtime-bakers-into-assets.md.
FROM python:3.11-slim

COPY scripts/build_offline_basemap.py /app/build_offline_basemap.py
COPY scripts/build_offline_terrain.py /app/build_offline_terrain.py
```

- [ ] **Step 2: Build it**

Run: `docker build -f bakers/tiles/Dockerfile -t sentinel-tiles-baker:offline .`
Expected: build succeeds.

- [ ] **Step 3: Verify scripts import cleanly**

Run: `docker run --rm sentinel-tiles-baker:offline python /app/build_offline_basemap.py --help`
Expected: prints argparse usage including `--zoom`, `--out`, `--concurrency` (no traceback).

- [ ] **Step 4: Commit**

```bash
git add bakers/tiles/Dockerfile
git commit -m "feat(bakers): add shared tiles (basemap+terrain) baker image"
```

---

### Task 4: Add baker services to compose; remove old init services

**Files:**
- Modify: `docker-compose.yml` (remove `dem-assets` service block; remove `osrm-assets` service block; append four baker services)

- [ ] **Step 1: Remove the `dem-assets` service block**

Delete the entire `dem-assets:` service definition (the comment header beginning `# Auto-bake worldwide Copernicus GLO-30 DEM into the image...` through the `- ./assets/dem:/data/dem` volume line). Use an Edit replacing that block with a single comment line:

```yaml
  # dem-assets / osrm-assets build-time bakers were replaced by runtime
  # baker services (dem-baker / osrm-baker) under the `bake` profile —
  # see the end of this file and docs/decisions/why-runtime-bakers-into-assets.md.
```

- [ ] **Step 2: Remove the `osrm-assets` service block**

Delete the entire `osrm-assets:` service definition (its comment header through the `- ./assets/osrm:/data` volume line). Remove it cleanly (the consolidated comment from Step 1 already covers both).

- [ ] **Step 2b: Run a parse check (expected to FAIL until bakers added)**

Run: `docker compose config >/dev/null && echo PARSE-OK`
Expected at this point: PARSE-OK (removing services does not break parse). If any other service still references `dem-assets`/`osrm-assets`, the next task fixes it; note the error.

- [ ] **Step 3: Append the four baker services** at the end of the `services:` section (immediately before the top-level `volumes:` key)

```yaml
  # ---------------------------------------------------------------------
  # Asset bakers (Compose profile `bake`). Run ONCE on a connected host to
  # populate ./assets, then ship ./assets to the air-gapped target. They are
  # hidden from the default `docker compose up` (air-gap rule). Each writes
  # directly to its host bind mount, so a crash/Ctrl-C/OOM never rolls back —
  # re-run to resume. See docs/deployment/{osrm-planet,dem-glo30}-bake.md.
  osrm-baker:
    profiles: ["bake"]
    build:
      context: .
      dockerfile: bakers/osrm/Dockerfile
    image: sentinel-osrm-baker:offline
    restart: "no"
    environment:
      DATA_DIR: /data
      PROFILE: /opt/car.lua
      PLANET_PBF_URL: "${PLANET_PBF_URL:-https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf}"
    volumes:
      - ./assets/osrm:/data

  dem-baker:
    profiles: ["bake"]
    build:
      context: .
      dockerfile: bakers/dem/Dockerfile
    image: sentinel-dem-baker:offline
    restart: "no"
    command:
      - python3
      - /app/build_offline_dem.py
      - --out=/data/dem
      - --concurrency=${DEM_CONCURRENCY:-8}
      - --lat-min=${DEM_LAT_MIN:--90}
      - --lat-max=${DEM_LAT_MAX:-90}
      - --lon-min=${DEM_LON_MIN:--180}
      - --lon-max=${DEM_LON_MAX:-180}
    volumes:
      - ./assets/dem:/data/dem

  basemap-baker:
    profiles: ["bake"]
    build:
      context: .
      dockerfile: bakers/tiles/Dockerfile
    image: sentinel-tiles-baker:offline
    restart: "no"
    command:
      - python
      - /app/build_offline_basemap.py
      - --out=/out
      - --zoom=${BASEMAP_ZOOM:-0-10}
      - --concurrency=${BASEMAP_CONCURRENCY:-16}
    volumes:
      - ./assets/static/basemap:/out

  terrain-baker:
    profiles: ["bake"]
    build:
      context: .
      dockerfile: bakers/tiles/Dockerfile
    image: sentinel-tiles-baker:offline
    restart: "no"
    command:
      - python
      - /app/build_offline_terrain.py
      - --out=/out
      - --zoom=${TERRAIN_ZOOM:-0-10}
      - --concurrency=${TERRAIN_CONCURRENCY:-4}
    volumes:
      - ./assets/static/terrain:/out
```

- [ ] **Step 4: Verify bakers are hidden by default but visible under the profile**

Run: `docker compose config --services | sort`
Expected: does NOT list `osrm-baker`/`dem-baker`/`basemap-baker`/`terrain-baker`, and no longer lists `dem-assets`/`osrm-assets`.

Run: `docker compose --profile bake config --services | sort`
Expected: lists all four `*-baker` services.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): add bake-profile baker services; drop dem-assets/osrm-assets"
```

---

### Task 5: Rewire runtime services (depends_on, assets mounts, healthcheck)

**Files:**
- Modify: `docker-compose.yml` (`backend` depends_on; `worker` depends_on; `osrm` depends_on; `assets` volumes + healthcheck)

- [ ] **Step 1: Remove `dem-assets` from `backend.depends_on`**

In the `backend` service, delete these two lines:

```yaml
      dem-assets:
        condition: service_completed_successfully
```

- [ ] **Step 2: Remove `dem-assets` from `worker.depends_on`**

In the `worker` service, delete the identical two lines.

- [ ] **Step 3: Remove `osrm-assets` from `osrm.depends_on`**

In the `osrm` service, delete:

```yaml
    depends_on:
      osrm-assets:
        condition: service_completed_successfully
```

(Remove the whole `depends_on:` key for `osrm` since it had only that one entry.)

- [ ] **Step 4: Bind-mount baked tiles into the `assets` nginx service**

In the `assets` service `volumes:` list, add these two read-only bind mounts (alongside the existing `reference_corpora_data` / `calibration_data` mounts):

```yaml
      # Basemap + terrain tile pyramids served from the host bind mount
      # (populated by basemap-baker / terrain-baker). Empty until baked —
      # nginx returns 404 for tiles and the relaxed healthcheck still passes.
      - ./assets/static/basemap:/usr/share/nginx/html/basemap:ro
      - ./assets/static/terrain:/usr/share/nginx/html/terrain:ro
```

- [ ] **Step 5: Relax the `assets` service healthcheck**

Replace the `assets` healthcheck `test` line:

```yaml
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1/healthz && wget -q -O /dev/null http://127.0.0.1/basemap/0/0/0.png && wget -q -O /dev/null http://127.0.0.1/terrain/0/0/0.png && wget -q -O /dev/null http://127.0.0.1/calibration/model_temperatures.json || exit 1"]
```

with (drops the basemap/terrain tile probes; keeps healthz + calibration):

```yaml
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1/healthz && wget -q -O /dev/null http://127.0.0.1/calibration/model_temperatures.json || exit 1"]
```

- [ ] **Step 6: Verify the resolved config has no dangling references**

Run: `docker compose config >/dev/null && echo CONFIG-OK`
Expected: CONFIG-OK with no warnings about undefined services.

Run: `docker compose config | grep -A3 'assets/static/basemap'`
Expected: shows the read-only basemap bind mount under the `assets` service.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): runtime services read ./assets; relax assets healthcheck"
```

---

### Task 6: Rewrite `assets/Dockerfile` to drop the basemap/terrain bake

**Files:**
- Modify: `assets/Dockerfile`

- [ ] **Step 1: Remove the basemap fetch stage**

Delete lines (the `# Basemap tiles —` comment block through its `cp -an /cache/basemap/. /work/static/basemap/`):

```dockerfile
# Basemap tiles — into a cache mount so re-runs are seconds, then
# mirrored into /work for stage 2 to COPY.
RUN --mount=type=cache,target=/cache/basemap \
    if [ -d /seed/basemap ]; then cp -an /seed/basemap/. /cache/basemap/ || true; fi \
 && python /build/build_offline_basemap.py \
        --zoom        "${BASEMAP_ZOOM}" \
        --concurrency "${BASEMAP_CONCURRENCY}" \
        --out         /cache/basemap \
 && mkdir -p /work/static/basemap \
 && cp -an /cache/basemap/. /work/static/basemap/
```

- [ ] **Step 2: Remove the terrain fetch stage**

Delete:

```dockerfile
# Terrain tiles — separate cache mount, lower concurrency by default
# because OpenTopoMap rate-limits more aggressively than CARTO.
RUN --mount=type=cache,target=/cache/terrain \
    if [ -d /seed/terrain ]; then cp -an /seed/terrain/. /cache/terrain/ || true; fi \
 && python /build/build_offline_terrain.py \
        --zoom        "${TERRAIN_ZOOM}" \
        --concurrency "${TERRAIN_CONCURRENCY}" \
        --out         /cache/terrain \
 && mkdir -p /work/static/terrain \
 && cp -an /cache/terrain/. /work/static/terrain/
```

- [ ] **Step 3: Remove the basemap/terrain COPY lines in the final stage**

Delete:

```dockerfile
COPY --from=fetcher /work/static/basemap/ /usr/share/nginx/html/basemap/
COPY --from=fetcher /work/static/terrain/ /usr/share/nginx/html/terrain/
```

- [ ] **Step 4: Drop basemap/terrain from the build-time `test` assertions**

Replace the multi-line `RUN test -f ...` block:

```dockerfile
RUN test -f /usr/share/nginx/html/basemap/0/0/0.png \
 && test -f /usr/share/nginx/html/terrain/0/0/0.png \
 && test -f /usr/share/nginx/html/fonts/ibm-plex-sans-400.woff2 \
 && test -s /usr/share/nginx/html/basemap/ATTRIBUTION.txt \
 && test -s /usr/share/nginx/html/terrain/ATTRIBUTION.txt \
 && test -f /opt/baked-reference-chips/MANIFEST.sha256 \
 && test -f /opt/baked-calibration/MANIFEST.sha256
```

with:

```dockerfile
RUN test -f /usr/share/nginx/html/fonts/ibm-plex-sans-400.woff2 \
 && test -f /opt/baked-reference-chips/MANIFEST.sha256 \
 && test -f /opt/baked-calibration/MANIFEST.sha256
```

- [ ] **Step 5: Relax the image `HEALTHCHECK`**

Replace:

```dockerfile
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/healthz \
     && wget -q -O /dev/null http://127.0.0.1/basemap/0/0/0.png \
     && wget -q -O /dev/null http://127.0.0.1/terrain/0/0/0.png \
     && wget -q -O /dev/null http://127.0.0.1/calibration/model_temperatures.json \
     || exit 1
```

with:

```dockerfile
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/healthz \
     && wget -q -O /dev/null http://127.0.0.1/calibration/model_temperatures.json \
     || exit 1
```

- [ ] **Step 6: Remove now-unused build args + COPY for basemap/terrain scripts**

Delete the two unused `ARG` lines near the top:

```dockerfile
ARG BASEMAP_ZOOM=0-10
ARG BASEMAP_CONCURRENCY=16
ARG TERRAIN_ZOOM=0-10
ARG TERRAIN_CONCURRENCY=4
```

and the two now-unused COPY lines:

```dockerfile
COPY scripts/build_offline_basemap.py        /build/build_offline_basemap.py
COPY scripts/build_offline_terrain.py        /build/build_offline_terrain.py
```

Also drop the matching `args:` entries (`BASEMAP_ZOOM`, `BASEMAP_CONCURRENCY`, `TERRAIN_ZOOM`, `TERRAIN_CONCURRENCY`) from the `assets` service `build.args` in `docker-compose.yml` (they are now baker env, not assets build args).

- [ ] **Step 7: Build the assets image — must succeed fast with no tile fetch**

Run: `time docker compose build assets`
Expected: build succeeds in seconds (no basemap/terrain download); no error about missing `/work/static/basemap`.

- [ ] **Step 8: Commit**

```bash
git add assets/Dockerfile docker-compose.yml
git commit -m "refactor(assets): serve basemap/terrain from bind mount; drop build-time tile bake"
```

---

### Task 7: Delete the obsolete init-container directories

**Files:**
- Delete: `osrm-assets/` (Dockerfile + scripts/entrypoint.sh)
- Delete: `dem-assets/` (Dockerfile + scripts/entrypoint.sh)

- [ ] **Step 1: Confirm nothing else references them**

Run: `grep -rn "osrm-assets\|dem-assets" docker-compose.yml backend/ scripts/ assets/ bakers/ 2>/dev/null | grep -v "assets/dem\|assets/osrm"`
Expected: no service/build references remain (matches on the `./assets/dem` / `./assets/osrm` host paths are fine and excluded above).

- [ ] **Step 2: Remove the directories**

```bash
git rm -r osrm-assets dem-assets
```

- [ ] **Step 3: Verify config still resolves**

Run: `docker compose config >/dev/null && docker compose --profile bake config >/dev/null && echo OK`
Expected: OK.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove obsolete osrm-assets/dem-assets build-time bakers"
```

---

### Task 8: Reclaim orphaned cache + smoke-test a baker

**Files:** none (operational verification)

- [ ] **Step 1: Reclaim the orphaned BuildKit cache**

Run: `docker buildx du | tail -3` (note Reclaimable ~1.1 TB), then:
`docker buildx prune -f`
Expected: reclaims ~1.1 TB; `df -h /` afterward shows free space jump from ~301 GB toward ~1.4 TB.

- [ ] **Step 2: Smoke-test the DEM baker into a THROWAWAY dir (not ./assets)**

This proves the baker writes correctly without touching the protected `./assets` tree or triggering a worldwide fetch. Use a 2°×2° land bbox:

```bash
mkdir -p /tmp/baker-smoke/dem
docker run --rm \
  -v /tmp/baker-smoke/dem:/data/dem \
  sentinel-dem-baker:offline \
  python3 /app/build_offline_dem.py --out=/data/dem \
    --lat-min=27 --lat-max=29 --lon-min=86 --lon-max=88
ls /tmp/baker-smoke/dem/glo30/*.tif | head && ls /tmp/baker-smoke/dem/glo30.vrt
```
Expected: a few `Copernicus_DSM_*.tif` tiles and `glo30.vrt` exist. (Everest bbox → guaranteed land tiles.)

- [ ] **Step 3: Smoke-test the tiles baker into a throwaway dir at z0-2**

```bash
mkdir -p /tmp/baker-smoke/basemap
docker run --rm \
  -v /tmp/baker-smoke/basemap:/out \
  sentinel-tiles-baker:offline \
  python /app/build_offline_basemap.py --out=/out --zoom=0-2 --concurrency=4
test -f /tmp/baker-smoke/basemap/0/0/0.png && echo BASEMAP-OK
```
Expected: BASEMAP-OK.

- [ ] **Step 4: Resumability check**

Re-run the Step 3 command. Expected: log shows `skip` for already-present tiles (ok=0 / skip>0), proving idempotent resume.

- [ ] **Step 5: Clean up the throwaway dir**

```bash
rm -rf /tmp/baker-smoke
```

No commit (operational task).

---

### Task 9: Documentation

**Files:**
- Create: `docs/decisions/why-runtime-bakers-into-assets.md`
- Modify: `docs/deployment/osrm-planet-bake.md`, `docs/deployment/dem-glo30-bake.md`, `docs/deployment/offline-airgap-deployment.md`
- Modify: `docs/scripts/build-offline-basemap.md`, `docs/scripts/build-offline-terrain.md`
- Modify/replace module docs that referenced `dem-assets`/`osrm-assets` images
- Modify: `docs/INDEX.txt`

- [ ] **Step 1: Write the decision doc**

Create `docs/decisions/why-runtime-bakers-into-assets.md` following the project doc shape (Path/Lines/Depends on/Purpose/Why this design/Key symbols/Inputs/Outputs/Failure modes/Cross-references). Core content: build-time `RUN` cannot write the host `./assets`; killed/cancelled RUN steps roll back cache-mount writes (causing PBF re-download and orphaned ~1.1 TB cache); 30 GB host + `systemd-oomd` cannot run a build-cgroup planet/Asia extract. Resolution: `bake`-profile runtime bakers writing straight to `./assets` (resumable, swap-capable), runtime reads `./assets`, air-gap preserved.

- [ ] **Step 2: Rewrite the two bake runbooks**

In `docs/deployment/osrm-planet-bake.md` and `docs/deployment/dem-glo30-bake.md`, replace the build-time/`docker compose build` instructions with the baker workflow:
```bash
docker compose --profile bake up osrm-baker   # or dem-baker
```
Document region selection (`PLANET_PBF_URL`, `DEM_LAT/LON_*`), resumability, and that output lands in `./assets/osrm` / `./assets/dem`. Note the 30 GB-host constraint and regional-extract guidance for OSRM.

- [ ] **Step 3: Update the air-gap runbook**

In `docs/deployment/offline-airgap-deployment.md`, insert the prep ordering: `docker buildx prune -f` → `docker compose --profile bake up ...` → `docker compose up -d --build` → ship `./assets` + images → `docker compose up -d` on target.

- [ ] **Step 4: Update the basemap/terrain script docs**

In `docs/scripts/build-offline-basemap.md` and `build-offline-terrain.md`, note they now run inside `basemap-baker`/`terrain-baker` writing to `./assets/static/{basemap,terrain}`, served read-only by the `assets` nginx.

- [ ] **Step 5: Fix module-doc cross-references**

Run: `grep -rln "dem-assets\|osrm-assets" docs/`
For each hit, update to the new baker services / `./assets` read paths. Update or replace the module docs for `assets/Dockerfile`, `docker-compose.yml`, and create module docs for the three `bakers/*/Dockerfile`.

- [ ] **Step 6: Update `docs/INDEX.txt`**

Add one sorted line for `decisions/why-runtime-bakers-into-assets.md` and any new baker module docs; fix any renamed entries. Keep entries ≤100 chars, tags from the fixed vocabulary.

- [ ] **Step 7: Product-tour check (expected no-op)**

Run: `grep -rl "data-tour" frontend/src/components/ | head`
Expected: no frontend interactive controls changed by this work; confirm no tour steps need edits. (Infra-only change.)

- [ ] **Step 8: Commit**

```bash
git add docs/
git commit -m "docs: runtime asset bakers — decision, runbooks, module docs, INDEX"
```

---

### Task 10: Full-config acceptance

**Files:** none (acceptance)

- [ ] **Step 1: Default config performs no downloads and references no removed services**

Run: `docker compose config --services | sort` and confirm no `*-assets`/`*-baker` services; `docker compose config` resolves clean.

- [ ] **Step 2: Build the full default stack (no map fetching)**

Run: `docker compose build`
Expected: all images build; `assets` builds in seconds; no basemap/terrain/DEM/OSRM downloads occur.

- [ ] **Step 3: Bring up the stack with EMPTY ./assets (graceful degradation)**

Run: `docker compose up -d` then after start `docker compose ps`
Expected: `assets` healthy (via `/healthz`), `osrm` unhealthy or restarting (no `planet.osrm`) but the rest of the stack is up. `curl localhost:3000/api/analytics/...` route/viewshed endpoints return 503 (honest-missing), not 500.

- [ ] **Step 4: (Operator, optional, real bake) Populate ./assets and re-verify**

On the connected host with `.env` set (`PLANET_PBF_URL` regional):
```bash
docker compose --profile bake up dem-baker basemap-baker terrain-baker osrm-baker
docker compose up -d
```
Expected: `osrm` becomes healthy, `/route/v1/...` returns a route; basemap/terrain tiles serve at `localhost:3000/basemap/0/0/0.png`. Interrupting `osrm-baker` mid-extract and re-running does NOT re-download the PBF (it resumes).

- [ ] **Step 5: Final commit (if any acceptance fixups were needed)**

```bash
git add -A && git commit -m "fix: runtime asset baker acceptance fixups"
```

---

## Self-Review

**Spec coverage:**
- Baker services (osrm/dem/basemap/terrain) → Tasks 1-4. ✓
- Runtime reads ./assets, depends_on removed, healthcheck relaxed → Task 5. ✓
- assets image drops basemap/terrain bake; nginx serves from bind mount → Task 6. ✓
- Remove dem-assets/osrm-assets services + dirs → Tasks 4 & 7. ✓
- Disk reclaim (~1.1 TB), no migration → Task 8. ✓
- Resumability / swap / no-rollback → Tasks 1, 8 (smoke resume), 10 (real bake). ✓
- Air-gap: bakers hidden behind profile → Task 4 Step 4, Task 10 Step 1. ✓
- OSRM PBF kept after extract → inherent (script keeps planet.osm.pbf; no deletion step added). ✓
- Docs (decision, runbooks, airgap, scripts, module, INDEX, tour) → Task 9. ✓
- Failure modes (empty assets → 503, healthy stack) → Task 10 Step 3. ✓

**Placeholder scan:** No TBD/TODO; every code/Dockerfile/compose step shows full content; verification commands have expected output.

**Type/name consistency:** Image tags (`sentinel-osrm-baker:offline`, `sentinel-dem-baker:offline`, `sentinel-tiles-baker:offline`) and service names (`osrm-baker`, `dem-baker`, `basemap-baker`, `terrain-baker`) are used identically across Tasks 1-4, 8, 10. Bind-mount targets (`/data`, `/data/dem`, `/out`) match each script's `--out`/`DATA_DIR`. `command:` uses `--flag=value` form so leading-dash bounds (e.g. `--lat-min=-90`) parse correctly.

**Gap check:** `.env` already carries `PLANET_PBF_URL` (Asia) from prior work — no new task needed. Fonts/reference-corpora/calibration intentionally remain baked (Non-goals) — assets Dockerfile edits in Task 6 leave those stages intact.
