# Decision: raise PostGIS `max_connections` to 300

## Context

With the imagery worker now running a full scene to completion (thousands of
detections persisted, each with ontology label upserts) **concurrently** with an
FMV clip — instead of the old false-success that finished imagery in seconds with
zero DB work — the stack's simultaneous PostGIS connection demand jumped. The
default `max_connections=100` was exceeded and FMV failed with:

```
psycopg2.OperationalError: connection to server at "postgis" ... FATAL: sorry, too many clients already
```

Connection accounting at the spike: each Celery **prefork child** holds its own
PostGIS pool (`_reset_db_pool_after_fork`, see
[reset-db-pool-after-fork.md](reset-db-pool-after-fork.md)), plus Martin (~20
idle), the backend, and the tile/embed paths. Stopping the worker dropped live
connections from 100 back to ~35, confirming a **transient concurrent spike, not
a leak** — the pools free as tasks finish.

## Decision

Set `max_connections` on the postgis service via a `command` override, env-tunable:

```yaml
command: ["postgres", "-c", "max_connections=${POSTGIS_MAX_CONNECTIONS:-300}"]
```

300 gives ~3× headroom over the observed ~117-connection steady state under
concurrent imagery+FMV. Each backend slot costs ~10 MB, so 300 ≈ 3 GB worst
case — negligible on a GEOINT-class host with datacenter GPUs.

## Alternatives considered

- **Shrink per-child worker pools.** More surgical but risks throttling DB-bound
  tasks, and the right pool size depends on workload; raising the server ceiling
  is the standard, lower-risk lever.
- **Leave at 100.** Rejected: FMV (or any second DB-heavy task) fails whenever a
  large imagery scene is mid-store — exactly the imagery+FMV-together upload the
  platform supports.

## Consequences

- Imagery and FMV uploaded together both complete (verified: peak ~117
  connections under 300, both jobs succeed, zero inference restarts).
- Operators on small hosts can lower `POSTGIS_MAX_CONNECTIONS`; those running
  many concurrent ingests can raise it.

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md) — the pooled PostGIS/Neo4j stack.
- [reset-db-pool-after-fork.md](reset-db-pool-after-fork.md) — why each prefork child has its own pool.
- [why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md) — the change that made imagery run to full completion concurrently with FMV, surfacing this.
