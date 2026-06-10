# Why rustworkx for in-memory graph metrics (with a pure-Python fallback)

**Decision date:** 2026-06-10
**Status:** active

## Context

The Link Graph workspace needed global metrics — density, connected components, and degree / betweenness / PageRank centrality — to tell an analyst *which* entities are structurally central. Computing these as Cypher is awkward and slow; the natural approach is to pull a bounded snapshot into memory and analyse it there, exactly the pattern city2graph uses via its `nx_to_rx` rustworkx interop.

## Decision

Inherit the graph-analytics pattern from the open-source **city2graph** library (BSD-3): pull a ≤1500-node Neo4j snapshot into memory and compute metrics on it. Use `rustworkx` (a compiled Rust graph library, added to [backend/requirements.txt](../../backend/requirements.txt) as `rustworkx>=0.14`) as the fast path, and ship a **dependency-free pure-Python fallback** (union-find components + Brandes betweenness + power-iteration PageRank) so the endpoint works even before the image is rebuilt to include rustworkx.

Why a fallback at all rather than just adding the dependency: rustworkx is a wheel that lands only on the next image build, and an air-gapped deployment may be running an older image. The fallback is correct (just slower) for the bounded snapshot sizes the endpoint returns, so `/api/graph/metrics` never 500s for a missing wheel. The response's `backend` field reports which path ran.

The `nx_to_rx` / `rx_to_nx` interop helpers mirror city2graph's surface but are not on the request path — they require both rustworkx and networkx.

## Consequences

**Positive**
- Sub-millisecond metrics on the fast path; always-available correct answers on the fallback.
- No hard dependency on rustworkx for the route to function.

**Negative / accepted**
- Two code paths to keep in agreement (mitigated: both are exercised by the same `compute_metrics` contract and unit tests).
- The pure-Python Brandes/PageRank would not scale past a few thousand nodes — fine, because the snapshot is explicitly bounded.

## Related

- [backend/graph-metrics.md](../backend/graph-metrics.md) — module reference
- [backend-routers/graph-router.md](../backend-routers/graph-router.md) — `GET /api/graph/metrics`
- [decisions/why-proximity-colocation-graph.md](why-proximity-colocation-graph.md) — sibling city2graph-inherited capability
- [decisions/why-postgis-and-neo4j-coexist.md](why-postgis-and-neo4j-coexist.md)
