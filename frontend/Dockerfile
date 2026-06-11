FROM node:22-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# Martin vector-tile detection layer — DEFAULT ON. Persisted detection boxes
# render from MVT tiles + markers from the lite feed. Set to 0 via the compose
# build arg to restore the legacy fat-geojson per-feature-polygon path.
# See docs/decisions/why-detection-mvt-tiles.md.
ARG VITE_DETECTION_TILES=1
ENV VITE_DETECTION_TILES=$VITE_DETECTION_TILES

RUN npm run build

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "3000"]
