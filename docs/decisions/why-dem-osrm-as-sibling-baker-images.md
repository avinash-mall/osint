# Why DEM + OSRM ship as sibling baker images, not folded into `assets`

**Decision date:** 2026-05-28
**Status:** superseded — see [why-runtime-bakers-into-assets.md](why-runtime-bakers-into-assets.md) (2026-05-30)

## Context

The reference-corpora bake established a precedent: large air-gappable datasets fetched during `docker build`, baked into the [assets/Dockerfile](../../assets/Dockerfile) image, and rsynced onto a named volume by the image's entrypoint on first container start (see [why-bake-reference-corpora-into-assets.md](why-bake-reference-corpora-into-assets.md)). That decision argued for *extending* the existing `assets` image rather than creating a sibling, because the corpora payload was modest (5-10 GB) and assets was already the canonical "all baked static content" image.

When the same auto-bake-on-build pattern was extended to the worldwide Copernicus GLO-30 DEM (~150 GB) and the planet OSRM MLD dataset (~150-200 GB), the size budget changed by two orders of magnitude. The same fold-into-assets approach would have produced a 350+ GB nginx image.

## Decision

DEM and OSRM are baked into **two new sibling images** — `sentinel-dem-assets:offline` and `sentinel-osrm-assets:offline` — each with its own slim alpine final stage, its own entrypoint, and its own one-shot Compose service that runs as an init container.

The reference-corpora bake stays in `assets`.

## Why sibling images, not extend `assets`

- **`assets` is an nginx server.** It runs continuously, serving basemap / terrain / fonts / reference-chips over HTTP. The image is loaded into memory by nginx workers and held there for the lifetime of the stack. Folding 350 GB of non-served data into the same image bloats nginx's container without benefit — none of that data is HTTP-served by nginx, and the DEM/OSRM consumers (backend, osrm-routed) access the data through filesystem volumes, not HTTP.
- **Independent lifecycles.** Operators may want to refresh OSRM (planet PBF update, every few months) without re-baking the GLO-30 DEM (essentially static). Likewise the corpora and basemap re-bake on a different cadence than terrain. Sibling images give each lifecycle its own re-build target.
- **Per-bake base images.** The DEM fetcher needs GDAL (`gdalbuildvrt`); the OSRM fetcher needs `osrm-extract`. Each sibling can use the most appropriate base image for its build stage without polluting `assets` with multi-GB toolchains it doesn't need.
- **Init container semantics.** The new images are designed to run *once* and exit 0 (downstream services wait via `condition: service_completed_successfully`). The `assets` service stays running as nginx. Mixing one-shot and long-running semantics in a single image is awkward.
- **Air-gap shipping is per-image anyway.** `docker save sentinel-dem-assets:offline | gzip` ships the DEM as one tarball; `docker save sentinel-osrm-assets:offline | gzip` ships OSRM as another. Operators can choose which datasets to ship to which sites (an analyst with car routing but no terrain needs is a real use case).

## Trade-offs accepted

- **Three baker images instead of one.** Slight increase in compose complexity (two new services), and three `docker save | gzip` tarballs instead of one. Acceptable given the order-of-magnitude size difference between corpora and DEM/OSRM.
- **No HTTP-served fallback for DEM.** The basemap/terrain tile pyramids in `assets` are HTTP-served by nginx (frontend tile requests). The DEM is NOT served over HTTP — it lives only on the `dem_data` volume. This is correct: rasterio reads DEM via filesystem I/O, not vsi-curl, because random access into a 150 GB VRT mosaic is much faster over a local mount than over HTTP-range-reads.

## How to apply (superseded)

This section describes the original build-time pattern, which has been replaced. The `dem-assets/` and `osrm-assets/` directories have been removed. See [why-runtime-bakers-into-assets.md](why-runtime-bakers-into-assets.md) for the current operator workflow using `bakers/dem/` and `bakers/osrm/` with the `bake` Compose profile.

## Related

- [why-bake-reference-corpora-into-assets.md](why-bake-reference-corpora-into-assets.md) — the precedent set by the corpora bake; this decision diverges from it at scale.
- [why-glo30-as-default-dem.md](why-glo30-as-default-dem.md) — why GLO-30 specifically.
- [why-osrm-replaced-networkx.md](why-osrm-replaced-networkx.md) — why OSRM specifically.
- [deployment/dem-glo30-bake.md](../deployment/dem-glo30-bake.md), [deployment/osrm-planet-bake.md](../deployment/osrm-planet-bake.md) — operator runbooks.
