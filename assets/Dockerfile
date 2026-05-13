# Air-gap assets service: serves the offline raster basemap pyramid and
# self-hosted webfonts that previously came from fonts.googleapis.com /
# *.basemaps.cartocdn.com. Everything is baked in at build time on the
# connected host so the running container has zero outbound dependencies.
#
# Pre-bake steps (run once on the connected host, before `docker compose build`):
#   python scripts/build_offline_basemap.py --zoom 0-10 --out assets/static/basemap
#   bash assets/scripts/fetch_fonts.sh
FROM nginx:1.27.3-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY static/ /usr/share/nginx/html/

# Fail-fast: a forgotten pre-bake step aborts the build here instead of at
# first request seven minutes after a deploy. Tile 0/0/0.png is the root of
# the pyramid (always present after a successful basemap fetch); the IBM
# Plex Sans regular weight is the minimum payload the frontend needs.
RUN test -f /usr/share/nginx/html/basemap/0/0/0.png \
 && test -f /usr/share/nginx/html/fonts/ibm-plex-sans-400.woff2 \
 && test -s /usr/share/nginx/html/basemap/ATTRIBUTION.txt

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1

EXPOSE 80
