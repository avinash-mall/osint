# Operations — Imagery Ingest

## TL;DR

```bash
# Drop a raster on the shared volume and trigger ingest
curl -X POST http://localhost:3000/api/ingest \
  -H "Content-Type: application/json" \
  -b "sentinel_session=$COOKIE" \
  -d '{"image_url": "/data/imagery/incoming/sentinel2.tif", "sensor_type": "Optical"}'

# Or upload + ingest from the UI: Admin → Upload imagery
```

Remote HTTP(S) `image_url` ingest is disabled by default. For connected-host preparation only, set `ALLOW_REMOTE_IMAGERY_URLS=1`, optionally constrain `REMOTE_IMAGERY_ALLOWED_HOSTS`, and keep `REMOTE_IMAGERY_MAX_BYTES` sized for the staging volume.

## Sensor dropdown → modality + layers

The sensor choice in the UI maps to the request body sent to `/detect`:

| Selection | `modality` | `enabled_layers` |
|---|---|---|
| Optical (RGB) | `rgb` | `sam3, dota_obb, grounding_dino, dinov3_sat` |
| Multispectral | `multispectral` | `sam3, dinov3_sat` |
| Hyperspectral | `multispectral` (with UI warning) | `sam3, dinov3_sat` |
| SAR | `sar` | `sam3, terramind` |
| FMV | n/a → [fmv-ingest-pipeline.md](fmv-ingest-pipeline.md) | `sam3_video` or `yoloe` |

## What happens after submission

Six steps — see [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) for the full pipeline:

1. COG translate
2. Catalog the pass in PostGIS + Neo4j
3. Chip into 1008×1008 windows
4. POST chips to `inference-sam3:/detect`
5. Georeference results back to WGS84
6. Persist to PostGIS with mask RLE, embedding, provenance

Progress visible in:

- UI: Ingest workspace + Admin → Processing
- WebSocket: `ingest_progress` per chip, `ingest_complete` on done
- Polled: `GET /api/ingest/jobs/{task_id}`

## Tile URLs (through the nginx gateway)

```
# COG raster tiles (24 h cache)
http://localhost:3000/tiles/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/pass_cog.tif

# Vector tiles
http://localhost:3000/maps/detections/{z}/{x}/{y}
http://localhost:3000/maps/satellite_passes/{z}/{x}/{y}
http://localhost:3000/maps/ne_countries/{z}/{x}/{y}

# Offline basemap
http://localhost:3000/basemap/{z}/{x}/{y}.png
```

## Cross-references

- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
- [frontend/workspace-ingest.md](../frontend/workspace-ingest.md)
