# Air-Gap Deployment Runbook

This guide builds the Sentinel platform on a connected workstation and
deploys the resulting images to a fully disconnected server. After the
images are loaded, the platform runs without making any outbound network
calls — basemap tiles, AI model weights, and HLS segments are all served
from inside the cluster.

## What ships in the bundle

| Image                          | Built from               | Carries                                                           |
| ------------------------------ | ------------------------ | ----------------------------------------------------------------- |
| `sentinel-backend:latest`      | `./backend/Dockerfile`   | FastAPI, Celery, ffmpeg, `klvdata`, postgis client                |
| `sentinel-frontend:latest`     | `./frontend/Dockerfile`  | Vite-built static React bundle (`hls.js`, Leaflet, lucide, etc.)  |
| `sentinel-nginx:offline`       | `./nginx/Dockerfile`     | Reverse proxy **and** the baked-in Carto Dark basemap (z=0..10)   |
| `sentinel-inference-sam3:gpu`  | `./inference-sam3/Dockerfile.gpu` | CUDA 12.x + every HF weight under `/models/hf` (~18 GB)  |
| `postgis/postgis:16-3.4`       | upstream                 | PostGIS DB                                                        |
| `redis:alpine`                 | upstream                 | Celery broker                                                     |
| `neo4j:5.20.0`                 | upstream                 | Ontology graph                                                    |
| `developmentseed/titiler:latest`| upstream                | Imagery COG tile server                                           |
| `maplibre/martin:latest`       | upstream                 | Vector tile server from PostGIS                                   |

## Connected build host — one-time setup

```bash
# 0. Pre-requisites: Docker buildx, Python 3.11+, ~5 GB free disk in repo root,
#    plus the ~20 GB the inference image will occupy.

# 1. GPU + CUDA detection (writes SAM3_* vars into .env).
python scripts/configure_host.py

# 2. Hugging Face token with access to facebook/sam3 and facebook/dinov3-*
#    (both gated). Get one at https://huggingface.co/settings/tokens.
export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
echo "HF_TOKEN=$HF_TOKEN" >> .env

# 3. Fetch the basemap tile pyramid (~1.4 M files, ~3 GB, 4-8 hours).
#    Idempotent — safe to ctrl-c and restart.
python scripts/build_offline_basemap.py --zoom 0-10

# 4. Build every image. The sam3 build downloads ~18 GB of weights;
#    expect 30-60 minutes on a fast connection.
docker compose build
```

When the build finishes, sanity-check the image sizes:

```bash
docker images | grep -E 'sentinel-nginx|sentinel-inference-sam3'
# sentinel-nginx:offline           ~3.1 GB
# sentinel-inference-sam3:gpu      ~18-22 GB
```

## Package the bundle

```bash
docker save \
  sentinel-backend:latest \
  sentinel-frontend:latest \
  sentinel-nginx:offline \
  sentinel-inference-sam3:gpu \
  postgis/postgis:16-3.4 \
  redis:alpine \
  neo4j:5.20.0 \
  developmentseed/titiler:latest \
  maplibre/martin:latest \
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

# 3. Bring the .env file online from the template. HF_TOKEN must stay
#    empty here — the image already carries the weights.
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

## How to confirm the air-gap holds

```bash
# 1. No model fetches at runtime.
docker compose logs inference-sam3 | grep -iE 'downloading|http(s)?://' || \
  echo "OK — no outbound model fetches"

# 2. No basemap fetches from the browser.
#    Open the GEOINT or FMV tab and check the network panel: every
#    /basemap/* request should be 200 from your nginx, no requests to
#    basemaps.cartocdn.com.

# 3. End-to-end: drop a tiny .mp4 into FMV, confirm detections appear.
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

## Strict air-gap variant

By default `nginx/tile-proxy.conf` includes an `@basemap_fallback` location
that proxies missing tiles from `a.basemaps.cartocdn.com`. This means a
deployment without pre-baked tiles still renders maps (useful during build
or in connected QA environments). For a deployment that **must not** reach
the public internet, edit `nginx/tile-proxy.conf` before `docker compose
build`:

1. Delete the `location @basemap_fallback { ... }` block.
2. Change `try_files $uri @basemap_fallback;` to `try_files $uri =404;`.

After rebuilding the nginx image, any tile not in the baked pyramid 404s
instead of attempting an outbound HTTPS request. Verify with:

```bash
docker compose logs nginx | grep -i carto    # should be empty
```

A belt-and-braces approach for extra-strict environments: block outbound
DNS at the host firewall. The `@basemap_fallback` block's
`resolver 1.1.1.1 8.8.8.8` line will fail to resolve, and the fallback
short-circuits to a 502 rather than reaching out.

## Known limitations

- **LLM features**: ontology auto-update and any chat-model-driven flow
  fall back to `AIUnavailable` when `OPENAI_API_BASE` is unset. The UI
  works; only those specific actions skip.
- **URL ingest**: `/api/ingest/url` still accepts URLs, but the worker
  fetch fails immediately and logs the error. This is by design — the
  feature exists for connected deployments.
- **Basemap zoom ceiling**: tiles end at zoom 10 (~city-block scale).
  For street-level zoom, re-run `build_offline_basemap.py --zoom 0-14`
  and rebuild the nginx image. Each additional zoom level quadruples
  storage.
- **Carto attribution**: the platform displays `© OpenStreetMap
  contributors © CARTO` in the basemap layer credits (Leaflet's default
  attribution control). Don't remove it — CC-BY requires it.
