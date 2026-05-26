# Derived from the upstream PostGIS image. Adds pgvector so the Reference
# Embedding DB schema can use `vector(1024)` / `vector(512)` columns and
# HNSW indexes. The PGDG apt repo is already configured in the base image.
FROM postgis/postgis:18-3.6

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-18-pgvector \
 && rm -rf /var/lib/apt/lists/*
