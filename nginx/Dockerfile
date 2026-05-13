# Sentinel reverse proxy. Static assets (basemap tiles, webfonts) live in
# the sibling `assets` container; this image only carries the routing
# config and is rebuildable in seconds without internet access.
FROM nginx:1.27.3-alpine

COPY tile-proxy.conf /etc/nginx/conf.d/default.conf
