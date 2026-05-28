# Why Copernicus GLO-30 is the default DEM

**Decision date:** 2026-05-28
**Status:** active

## Context

`backend/terrain.py` ray-casts viewshed and line-of-sight against a single rasterio-readable DEM. The original default was `/data/dem/dem.tif` — a single GeoTIFF the operator had to supply themselves. In practice none was ever supplied, and the analytics panel surfaced "DEM resource is not configured" on every interaction.

The platform's coverage requirement is worldwide and the deployment posture is air-gap after build. That ruled out vsi-curl / on-demand fetch from a public mirror.

Options considered:

| Dataset | Native res | Worldwide size | Suitable for |
|---|---|---|---|
| ETOPO 2022 | 1 arc-min (~1.85 km) | ~440 MB | strategic visibility only |
| SRTM30+ | 30 arc-sec (~1 km) | ~330 MB | strategic visibility only |
| GMTED2010 | 7.5 arc-sec (~230 m) | ~7 GB | rough tactical visibility |
| Copernicus GLO-30 | 1 arc-sec (~30 m) | ~150 GB | tactical viewshed / LOS |
| Copernicus GLO-90 | 3 arc-sec (~90 m) | ~16 GB | low-tactical |

## Decision

GLO-30 is the default. The operator-chosen requirement was tactical-grade global coverage; coarser DEMs cannot distinguish buildings, ridgelines, or revetments and so are not useful for the defence-analyst viewshed/LOS use case that drove the change.

The 150 GB does not live inside the Docker image. The `dem-baker` Compose profile fetches the per-1°-tile GeoTIFFs from the AWS Open Data mirror (`s3://copernicus-dem-30m/`, unauthenticated HTTPS) into the `dem_data` named volume and then runs `gdalbuildvrt` to assemble them into `glo30.vrt`. `rasterio.open()` reads VRTs through the same path as a single GeoTIFF, so `backend/terrain.py` itself is unchanged beyond the new default path.

## Consequences

**Positive**

- Tactical-grade viewshed and LOS work worldwide.
- Air-gap deployment after the one-time bake; no runtime internet dependency.
- VRT mosaic is rasterio-transparent — module code stays simple.

**Negative / accepted trade-offs**

- ~150 GB on disk in the runtime host. Smaller deployments can override `DEM_PATH` to point at a regional GeoTIFF or a coarser global DEM (ETOPO 2022 / GLO-90).
- Initial bake is bandwidth-heavy (~6-24 h on a typical link). Idempotent so a crashed bake can be resumed.
- Ocean-only 1° cells are 404 on the mirror; those simply have no coverage in the VRT and `sample_elevation` returns `None`.

## Related

- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [deployment/dem-glo30-bake.md](../deployment/dem-glo30-bake.md)
- [decisions/why-osrm-replaced-networkx.md](why-osrm-replaced-networkx.md) — sibling decision for routing
