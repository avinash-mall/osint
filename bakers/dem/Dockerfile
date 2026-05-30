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
