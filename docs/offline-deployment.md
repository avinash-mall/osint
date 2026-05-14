# Air-Gap Deployment Runbook

This guide builds the Sentinel platform on a connected workstation and
deploys the resulting images to a fully disconnected server. After the
images are loaded, the platform runs without making any outbound network
calls — basemap tiles, AI model weights, webfonts, and HLS segments are
all served from inside the cluster.

> **SECURITY — rotate before building.** If the working tree's `.env`
> contains a real `HF_TOKEN`, treat that token as leaked: revoke it at
> <https://huggingface.co/settings/tokens>, issue a fresh one, then
> `git rm --cached .env`, add `.env` to `.gitignore`, and scrub the
> historical commit with `git filter-repo --invert-paths --path .env`.
> `.env` is operator-managed per-deployment and must never be committed.

## What ships in the bundle

| Image                            | Built from                          | Carries                                                          |
| -------------------------------- | ----------------------------------- | ---------------------------------------------------------------- |
| `sentinel-backend:latest`        | `./backend/Dockerfile`              | FastAPI, Celery, ffmpeg, `klvdata`, postgis client               |
| `sentinel-frontend:latest`       | `./frontend/Dockerfile`             | Vite-built static React bundle (`hls.js`, Leaflet, lucide, etc.) |
| `sentinel-nginx:offline`         | `./nginx/Dockerfile`                | Reverse proxy only (lightweight; rebuilds in seconds)            |
| `sentinel-assets:offline`        | `./assets/Dockerfile`               | Carto-Dark basemap pyramid (z=0..10) **and** IBM Plex webfonts   |
| `sentinel-inference-sam3:gpu`    | `./inference-sam3/Dockerfile.gpu`   | CUDA 12.x + every HF weight under `/models/hf` (~18 GB)          |
| `postgis/postgis:18-3.6`         | upstream                            | PostGIS DB                                                       |
| `redis:8-alpine`               | upstream                            | Celery broker                                                    |
| `neo4j:5.26.26-community-ubi10`                   | upstream                            | Ontology graph                                                   |
| `ghcr.io/developmentseed/titiler:2.0.2` | upstream                            | Imagery COG tile server                                          |
| `ghcr.io/maplibre/martin:1.9.1`| upstream (GHCR)                     | Vector tile server from PostGIS                                  |

All upstream images are pinned to specific versions. On the connected
host, record the `@sha256:...` digests for any image you intend to ship
so the air-gap target can verify byte-for-byte equivalence:

```bash
for img in postgis/postgis:18-3.6 redis:8-alpine neo4j:5.26.26-community-ubi10 \
           ghcr.io/developmentseed/titiler:2.0.2 ghcr.io/maplibre/martin:1.9.1; do
  docker pull "$img" && docker inspect --format '{{index .RepoDigests 0}}' "$img"
done
```

## Connected build host — one-time setup

```bash
# 0. Pre-requisites: Docker buildx, Python 3.11+, ~5 GB free disk in repo root,
#    plus ~25 GB across the inference and assets images.

# 1. GPU + CUDA detection (writes SAM3_* vars into .env).
python scripts/configure_host.py

# 2. Hugging Face token with access to facebook/sam3 and facebook/dinov3-*
#    (both gated). Get one at https://huggingface.co/settings/tokens.
#    Keep this OUT of git — write to .env which is in .gitignore.
export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
echo "HF_TOKEN=$HF_TOKEN" >> .env

# 3. Pre-bake the assets payload: basemap tile pyramid (~1.4 M files,
#    ~3 GB, 4-8 h) plus the IBM Plex webfont woff2 files.
#    Idempotent — safe to ctrl-c and restart.
python scripts/build_offline_basemap.py --zoom 0-10
bash assets/scripts/fetch_fonts.sh

# 4. Build every image. The sam3 build downloads ~18 GB of weights;
#    expect 30-60 minutes on a fast connection. The assets and inference
#    Dockerfiles both fail fast if the pre-bake step above was skipped.
docker compose build
```

When the build finishes, sanity-check the image sizes:

```bash
docker images | grep -E 'sentinel-(assets|inference-sam3|nginx)'
# sentinel-assets:offline          ~3.1 GB
# sentinel-inference-sam3:gpu      ~18-22 GB
# sentinel-nginx:offline           ~50 MB
```

## Package the bundle

```bash
docker save \
  sentinel-backend:latest \
  sentinel-frontend:latest \
  sentinel-nginx:offline \
  sentinel-assets:offline \
  sentinel-inference-sam3:gpu \
  postgis/postgis:18-3.6 \
  redis:8-alpine \
  neo4j:5.26.26-community-ubi10 \
  ghcr.io/developmentseed/titiler:2.0.2 \
  ghcr.io/maplibre/martin:1.9.1 \
  | gzip -1 > sentinel-offline-bundle.tar.gz
# Expect ~22-26 GB compressed.

# Bundle the compose files and supporting scripts.
tar czf sentinel-repo.tar.gz \
  docker-compose.yml .env.offline.example \
  backend/init_postgis.sql \
  nginx/tile-proxy.conf
```

Transfer `sentinel-offline-bundle.tar.gz` and `sentinel-repo.tar.gz` to
the air-gapped server (USB, sneakernet, internal SFTP, whatever).

## Offline target host

```bash
# 1. Load the images. ~5-10 minutes.
gunzip -c sentinel-offline-bundle.tar.gz | docker load

# 2. Lay down the compose + config files.
mkdir -p /opt/sentinel && cd /opt/sentinel
tar xzf /path/to/sentinel-repo.tar.gz

# 3. Bring the .env file online from the template. HF_TOKEN MUST stay
#    empty here — the image already carries the weights, and
#    HF_HUB_OFFLINE=1 will refuse any download attempt anyway.
cp .env.offline.example .env

# 4. Start the stack.
docker compose up -d

# 5. Wait for SAM3 to come up. Health check has a 420 s start_period
#    because torch.compile + CUDA warm-up takes time on first boot.
docker compose ps
```

Open `http://<host>:3000` in a browser. The GEOINT map should render
with dark tiles (served from `/basemap/...`), the FMV tab should accept
uploads and stream them via HLS, and the detection pipeline should run
end-to-end without any DNS resolution leaving the host.

## Verifying zero egress

The stack is designed so it *cannot* reach the public internet at
runtime. To prove it on the target host:

```bash
# 1. Internal-only network: docker forbids NAT and external DNS on it.
docker network create --internal --driver bridge sentinel-airgap

cat > docker-compose.airgap.yml <<'EOF'
networks:
  default:
    name: sentinel-airgap
    external: true
EOF
docker compose -f docker-compose.yml -f docker-compose.airgap.yml up -d

# 2. Click through every UI tab (Map, FMV, Detections, Ontology, Login).
#    In browser devtools → Network, filter for `googleapis|gstatic|cartocdn`:
#    expect ZERO hits. Every request must be same-origin (host:3000).

# 3. Independent packet capture on the host.
sudo tcpdump -i any -nn \
  'port 53 or (tcp and (port 80 or port 443)) and not (net 172.16.0.0/12 or host 127.0.0.1)' \
  -w /tmp/airgap.pcap &
SNIFF_PID=$!
# ...drive the UI for 5 minutes...
sudo kill $SNIFF_PID
sudo tcpdump -r /tmp/airgap.pcap | head
# Expected: empty. Any DNS (53) or outbound 443 is a regression.

# 4. Asset-bake sanity (one-shot):
docker run --rm sentinel-assets:offline \
  ls /usr/share/nginx/html/basemap/0/0/0.png /usr/share/nginx/html/fonts/

# 5. Confirm the inference image did not phone home at startup:
docker compose logs inference-sam3 | grep -iE 'downloading|http(s)?://' \
  || echo "OK — no outbound model fetches"
```

A belt-and-braces option: add an `iptables` LOG-then-DROP rule on the
docker bridge for any non-internal destination and tail
`journalctl -kf | grep AIRGAP-LEAK`. Expected: silent.

## Refreshing only the assets image

The reverse proxy caches the `assets` upstream for 30 days. After
rebuilding the assets image (new tiles, new fonts), flush the proxy:

```bash
python scripts/build_offline_basemap.py --zoom 0-10
bash assets/scripts/fetch_fonts.sh
docker compose build assets
docker compose up -d assets
docker compose restart nginx       # clears proxy_cache
```

## Dev override — keeping a writable model cache

The offline build bakes models into the inference image, which makes
incremental model swaps painful. For day-to-day development, layer a
compose override that re-mounts a writable named volume on top:

```yaml
# docker-compose.dev.yml
services:
  inference-sam3:
    volumes:
      - sam3_models:/models
    environment:
      SAM3_HF_HUB_OFFLINE: "0"
      SAM3_TRANSFORMERS_OFFLINE: "0"

volumes:
  sam3_models:
```

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

The volume shadows the image's `/models` directory, restoring the old
"first run downloads, subsequent runs reuse" loop.

## Known limitations

- **LLM features**: ontology auto-update and any chat-model-driven flow
  fall back to `AIUnavailable` when `OPENAI_API_BASE` is unset. The UI
  works; only those specific actions skip.
- **URL ingest**: `/api/ingest/url` still accepts URLs, but the worker
  fetch fails immediately and logs the error. This is by design — the
  feature exists for connected deployments.
- **Basemap zoom ceiling**: pre-baked tiles end at zoom 10 (~city-block
  scale). The Leaflet basemap layer sets `maxNativeZoom={10}` so the
  map upscales gracefully past z=10 (slightly blurry) rather than
  rendering blank squares. For native street-level detail, re-run
  `build_offline_basemap.py --zoom 0-14` and rebuild the assets image —
  each additional zoom level quadruples storage.
- **Non-Latin scripts**: the bundled IBM Plex weights cover Latin-1.
  Cyrillic/Arabic/CJK labels in user-entered content will fall back to
  the OS's `system-ui` font. Add the matching IBM Plex subset to
  `assets/scripts/fetch_fonts.sh` and a `unicode-range` `@font-face`
  block in `frontend/src/index.css` if non-Latin coverage is needed.
- **Carto attribution**: the platform displays `© OpenStreetMap
  contributors © CARTO` in the basemap layer credits (Leaflet's default
  attribution control). Don't remove it — CC-BY requires it.
- **IBM Plex license**: SIL OFL 1.1 — the `assets/scripts/fetch_fonts.sh`
  pulls the OFL.txt alongside the woff2 files; the assets image serves
  it at `http://<host>:3000/assets/LICENSE.txt`.
