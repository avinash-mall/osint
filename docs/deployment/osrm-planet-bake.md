# Planet OSRM Bake

One-time bake of a planet-scale OSRM dataset that backs `/api/analytics/routes`. Run once on a host with internet access; the resulting `osrm_data` volume is then air-gappable.

## Prerequisites

- Docker + Docker Compose
- ~250 GB free on the disk hosting `osrm_data` (planet PBF + extract/partition/customize outputs)
- ~16 GB RAM during the `osrm-extract` step (planet extract is memory-heavy)
- ~6-24 h end-to-end depending on CPU and link

## Bake

```bash
docker compose --profile bake-osrm up --build osrm-baker
```

This runs [`scripts/build_offline_osrm.sh`](../../scripts/build_offline_osrm.sh) inside the upstream `ghcr.io/project-osrm/osrm-backend:v6.0.0` image and walks the standard MLD pipeline:

1. `curl --continue-at -` of `planet-latest.osm.pbf` (~80 GB) from `PLANET_PBF_URL` (default `https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf`). Resumable.
2. `osrm-extract -p /opt/car.lua planet.osm.pbf` — the long step (~3-6 h on a fast CPU). Uses the unmodified car profile bundled with the OSRM image (pinning the image version pins the profile).
3. `osrm-partition planet.osrm`
4. `osrm-customize planet.osrm`

Each step is gated on its own outputs, so a crashed bake can be resumed by re-running the same command.

## Bring OSRM up

After the bake completes, the `osrm` service mounts `osrm_data:/data:ro` and runs `osrm-routed --algorithm mld /data/planet.osrm`:

```bash
docker compose up -d osrm
docker compose ps osrm
# wait for "healthy"
```

The backend's `routing.py` talks to `http://osrm:5000` inside the Compose network.

## Verify

```bash
curl -s http://localhost:3000/api/analytics/capabilities | jq
# expect: {"dem": ..., "routing": true, "demo_fixtures": false}

curl -s -X POST http://localhost:3000/api/analytics/routes \
  -H "Content-Type: application/json" \
  -d '{"observer":{"latitude":51.5074,"longitude":-0.1278},"destination":{"latitude":48.8566,"longitude":2.3522}}' \
  | jq '.result.features | length'
# expect: 1-3 (London → Paris)
```

The map workspace's Analytics panel should show `ROUTING · OK` in the bottom chip.

## Updating the planet

To pull a fresh planet:

```bash
docker compose down osrm
docker volume rm sentinel_osrm_data   # destroys ~200 GB; check the volume name in `docker volume ls`
docker compose --profile bake-osrm up --build osrm-baker
docker compose up -d osrm
```

If you only want to refresh the customization (e.g. profile change), keep the volume and re-run osrm-customize manually inside the OSRM image.

## Smaller bake (regional)

OSRM is region-agnostic. To bake a smaller area (e.g. Europe), point `PLANET_PBF_URL` at a Geofabrik regional extract:

```
PLANET_PBF_URL=https://download.geofabrik.de/europe-latest.osm.pbf \
docker compose --profile bake-osrm up --build osrm-baker
```

The runtime `osrm` service does not need to change — it still serves whatever PBF was baked.

## Cross-references

- [backend/routing-osrm.md](../backend/routing-osrm.md)
- [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md)
- [volume-mounts-and-paths.md](volume-mounts-and-paths.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)
