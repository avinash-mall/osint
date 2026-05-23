# Offline / Air-Gap Deployment

## Purpose

The build-once / load-and-go runbook for disconnected sites. Every basemap tile, webfont, AI weight baked into the images at build time.

## Connected host (build)

```bash
# 1. Detect host GPU + driver and write build settings to .env
python scripts/configure_host.py

# 2. Set HF_TOKEN in .env (required only when SAM3_WEIGHTS_SOURCE=official)
echo "HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" >> .env

# 3. Session secret + admin
echo "SESSION_SECRET=$(openssl rand -hex 32)" >> .env
echo "ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')" >> .env

# 4. Build (~30-90 min including ~3 GB basemap fetch + ~18 GB SAM3 weights)
docker compose build

# 5. Save images for transport
docker save $(docker compose config --images) | gzip > sentinel-bundle.tar.gz
```

## Disconnected host (load + run)

```bash
gunzip -c sentinel-bundle.tar.gz | docker load
docker compose up -d
```

## What's baked in

- Carto Dark Matter basemap (z=0..14), ~13 GB — see [decisions/why-basemap-z14-cap.md](../decisions/why-basemap-z14-cap.md)
- OpenTopoMap terrain tiles (z=0..14), ~22 GB
- IBM Plex webfonts + SIL OFL 1.1 license bundle
- All Hugging Face model weights listed in [inference/model-manifest.md](../inference/model-manifest.md)
- Natural Earth country polygons

## Runtime DNS verification

After `docker compose up`, verify zero outbound traffic with `tcpdump` and `docker network create --internal`. All upstream images pinned to specific digests for byte-for-byte reproducibility.

## Dev override

The offline image bakes weights into the container. For day-to-day inference iteration, layer a `docker-compose.dev.yml` with a writable `sam3_models` volume → restores the "first run downloads, subsequent runs reuse" loop.

## Cross-references

- [scripts/configure-host-gpu.md](../scripts/configure-host-gpu.md)
- [scripts/build-offline-basemap.md](../scripts/build-offline-basemap.md)
- [scripts/build-offline-terrain.md](../scripts/build-offline-terrain.md)
- [inference/model-manifest.md](../inference/model-manifest.md)
- [environment-variables-reference.md](environment-variables-reference.md)
