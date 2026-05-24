# Why Per-Entity DINOv3 Embedding Centroids

## Decision

Phase 5.J added an entity-level re-ID embedding column to
`operational_entities` (`re_id_embedding JSONB`) populated by
`worker.tick_aggregate_entity_embeddings` from the
`detection_tracks.embedding_anchor` rows the analyst attached via
`operational_entity_tracks`. The cosine branch of
`worker.tick_entity_resimilarity` reads these centroids to emit
`POSSIBLY_SAME_AS {source: 'embedding'}` candidate edges.

## Why centroid (not per-track cosine)

Two alternatives we rejected:

- **Per-track all-pairs cosine across entities.** O(T²) where T = total
  attached tracks. For a deployment with 10⁴ tracks, that's 5·10⁷
  comparisons per `tick_entity_resimilarity` run. The centroid approach
  is O(E²) where E = operational entities (typically 10²–10³). Cheaper
  by 3–4 orders of magnitude.
- **Median embedding instead of mean.** Marginally more robust to a
  single outlier track, but the cost is per-dimension sorting in pure
  Python (~50× slower than averaging on typical DINOv3-SAT
  1024-dim vectors). The aggregator already skips entities whose
  attached anchors have inconsistent dims, which catches the worst
  case.

Centroid (arithmetic mean of attached `embedding_anchor` vectors) is
the right cost/quality trade-off for an air-gappable system.

## Why analyst-attached, not automatic

The link from `operational_entities` to `detection_tracks` is via the
`operational_entity_tracks` association table — populated only by the
analyst-facing
`POST /api/operational-entities/{id}/attach-track/{track_id}` endpoint.

We considered automatic attachment via the existing
`(:Asset)-[:OBSERVED_AT]->(:Observation)` Neo4j chain, but observations
don't currently carry the underlying `detection_track_id`. Walking that
gap would require either:
- Adding `observations.detection_track_id` (schema change + ingest-path
  rewrite), or
- Joining observations to detections by `entity_id` and then detections
  to tracks (multi-hop, brittle if any link is missing).

Both grow the surface area significantly. Analyst-asserted attachment
is consistent with [decisions/why-llm-proposed-entities.md](why-llm-proposed-entities.md)
— never silently mutate identity. The aggregator gets richer data as
analysts use it; the cosine branch's confidence grows with adoption.

## Embedding storage choice

`re_id_embedding JSONB` (not `vector(1024)` or a binary blob):
- **Air-gap compatibility**: pgvector requires a Postgres extension; not
  every offline deployment can install one.
- **Dimensionality flexibility**: DINOv3-SAT today is 1024-dim but
  upstream may change; JSONB doesn't pin a dimension.
- **Cosine is pure-Python** ([backend/graph_writes.py](../backend/graph-writes.md)
  `cosine_similarity` helper), no extension needed. Performance is fine
  at entity-count scale (E² cosine over ≤1000 entities per kind = ≤10⁶
  ops, easy).
- **The `embedding_anchor` source column on `detection_tracks` is already
  JSONB** (see [backend/tracker.py:411](../../backend/tracker.py#L411)), so
  using the same format for entity centroids keeps the ingest pipeline
  symmetric.

`re_id_dim INT` is stored alongside for sanity checks (mismatched dim →
skip the cosine).

## Trade-offs accepted

- **Stale centroids until next aggregation**: when the analyst attaches a
  new track, the centroid lags until the next 12-h beat run. We accept
  this because (a) `attach-track` could trigger a `.delay()` later if
  latency becomes important, and (b) the analyst review of
  POSSIBLY_SAME_AS happens on a slower cadence than the aggregator
  anyway.
- **Heuristic branch still runs**: even when the embedding branch
  produces a pair, the name-match heuristic also runs. We dedupe via a
  sorted-id `seen_pairs` set; the embedding pair lands first because it
  iterates first.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 5.J.
- [decisions/why-llm-proposed-entities.md](why-llm-proposed-entities.md)
  — the analyst-asserted-identity principle this extends.
- [backend/graph-writes.md](../backend/graph-writes.md) — cosine_similarity helper.
- [backend/tracker.py#L411](../../backend/tracker.py#L411) — source of the JSONB anchor format.
