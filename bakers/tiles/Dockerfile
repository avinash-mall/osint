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
