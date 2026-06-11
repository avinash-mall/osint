FROM node:22-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# Opt-in Martin vector-tile detection layer (default off). Set to 1 via the
# compose build arg to render detections from MVT instead of per-feature
# polygons. See docs/decisions/why-detection-mvt-tiles.md.
ARG VITE_DETECTION_TILES=0
ENV VITE_DETECTION_TILES=$VITE_DETECTION_TILES

RUN npm run build

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "3000"]
