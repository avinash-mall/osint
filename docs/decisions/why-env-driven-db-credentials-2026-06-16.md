# Why Database Credentials Are Env-Driven (Fail-Closed)

**Date:** 2026-06-16
**Status:** Accepted

## Context

The 2026-05-31 hardening pass ([why-security-hardening-2026-05-31.md](why-security-hardening-2026-05-31.md))
made `ADMIN_PASSWORD` and `SESSION_SECRET` fail-closed but left the database
credentials as hardcoded literals in `docker-compose.yml`: `NEO4J_AUTH=neo4j/password`,
`POSTGRES_USER/PASSWORD=sentinel`, and three `POSTGIS_URI` / one Martin `DATABASE_URL`
DSNs embedding `sentinel:sentinel`. A follow-up technical audit flagged these as the
repo's one remaining "Critical": committed source shipping working credentials that get
deployed verbatim.

## Decision

Database passwords now come from `.env`, mirroring the established `${VAR:?...}` pattern:

- **neo4j:** `NEO4J_AUTH=${NEO4J_USERNAME:-neo4j}/${NEO4J_PASSWORD:?…}` — password required.
- **postgis:** `POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?…}` required; `POSTGRES_USER`/
  `POSTGRES_DB` keep the convenient `:-sentinel` default (a username/db-name is not a secret).
- **backend / worker / worker_beat:** `NEO4J_PASSWORD=${NEO4J_PASSWORD:?…}` and
  `POSTGIS_URI=postgresql://${POSTGRES_USER:-sentinel}:${POSTGRES_PASSWORD:?…}@postgis:5432/${POSTGRES_DB:-sentinel}`.
- **martin:** `DATABASE_URL` composed from the same vars.
- `.env.example` ships `NEO4J_PASSWORD=` / `POSTGRES_PASSWORD=` **empty** (like `ADMIN_PASSWORD=`).
- `backend/database.py` keeps `os.getenv(..., <dev default>)` fallbacks, now commented as
  **non-production** — they only fire outside compose (tests set their own DSNs; in compose the
  env is always present), so no deployed credential ships from source.

Only passwords are fail-closed; usernames/db-name default. A fresh deploy must set exactly two
secrets (`NEO4J_PASSWORD`, `POSTGRES_PASSWORD`) alongside the existing `ADMIN_PASSWORD`/`SESSION_SECRET`.

## The volume-auth gotcha (rotation)

Neo4j and Postgres apply their password **only on first init** of the data volume
(`neo4j_data`, `pg_data`). Changing the env var afterward does **not** rotate the live
password — the running stack keeps the volume's original. Therefore:

- **Fresh deploy:** set the passwords in `.env` before the first `docker compose up`; the
  volumes initialize with them. Done.
- **Existing stack (this host):** the gitignored `.env` carries the current live values
  (`NEO4J_PASSWORD=password`, `POSTGRES_PASSWORD=sentinel`) so `${…:?}` resolves and the stack is
  not disrupted — `docker compose up -d` does **not** recreate neo4j/postgis (resolved values are
  byte-identical). To actually move off the weak passwords without a volume wipe, rotate in place:

  ```bash
  # Neo4j (run inside the neo4j container, authenticating with the OLD password)
  docker compose exec neo4j cypher-shell -u neo4j -p '<old>' \
    "ALTER CURRENT USER SET PASSWORD FROM '<old>' TO '<new>';"

  # PostGIS (authenticating as the existing role)
  docker compose exec postgis psql -U sentinel -d sentinel \
    -c "ALTER ROLE sentinel PASSWORD '<new>';"

  # Then update NEO4J_PASSWORD / POSTGRES_PASSWORD in .env and restart the app
  # containers (NOT the DBs): docker compose up -d backend worker worker_beat martin
  ```

## Consequences

Committed source no longer contains deployable database credentials. Fresh deployments fail fast
with a clear message until the two passwords are provided. Existing volumes are unaffected and
rotate via the documented in-place procedure rather than a destructive re-init. The air-gap build
and runtime posture is unchanged.

## Cross-references

- [why-security-hardening-2026-05-31.md](why-security-hardening-2026-05-31.md)
- [../deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [../deployment/docker-compose-services.md](../deployment/docker-compose-services.md)
- [../deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
