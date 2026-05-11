FROM nginx:alpine

# Bake the offline raster basemap (z=0..10 Carto Dark) into the image.
# The directory is built on the connected host by:
#     python scripts/build_offline_basemap.py --zoom 0-10
# and is supplied at build time as the `assets` additional context (see
# docker-compose.yml). If the bake step has not been run, the COPY below
# fails fast at image build — exactly when an operator can notice.
COPY --from=assets basemap /usr/share/nginx/basemap

COPY tile-proxy.conf /etc/nginx/conf.d/default.conf
