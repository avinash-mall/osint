# Why LLM-Proposed Operational Entities (with Analyst Review)

## Decision

Phase 4 introduces operational entities — Vessel, Aircraft, Vehicle,
Facility, Unit, Asset — as analyst-curated by default, with an LLM/heuristic
proposer that drops candidates into ``entity_candidates`` for review. No
operational entity is ever silently created.

The proposer task ([`worker.tick_propose_entities`](../../backend/worker_legacy.py))
ships in Phase 4.F as a cheap offline heuristic (seeded from
``REPEATED_AT`` clusters that don't yet have a matching entity); the
LLM-driven variant is the upgrade slot, swappable without touching the
review flow.

## Why not "automatic clustering, no approval"?

That option was raised during brainstorming. We rejected it. A hallucinated
``Vessel "Black Pearl"`` that auto-merges 17 unrelated detections is a
worse failure than asking the analyst to review a queue:

- Wrong instance identity is hard to un-merge — once downstream
  automations read the SAME_AS edge as authoritative, "split this Vessel
  back into two" needs a manual sub-graph rewrite.
- The same open-vocabulary principle from
  [`why-open-vocabulary.md`](why-open-vocabulary.md) — "never silently
  mutate labels" — extends to entity identity. Never silently mint an
  identity that can later carry attribution.
- Operational identity decisions are *consequential*: an analyst review at
  proposal time is cheaper than an investigation later about why two
  detections that aren't the same Vessel got linked.

The plan calls for this explicitly:

> Recommendation: analyst-asserted as primary, LLM-proposed as secondary.

## How the candidate flow works

1. **Proposal sources** (write rows to ``entity_candidates`` with status='pending'):
   - ``worker.tick_propose_entities`` walks ``:REPEATED_AT`` clusters and
     emits a candidate when (a) the detection class maps to a known
     operational kind, (b) no matching operational entity already exists,
     and (c) no pending candidate with the same name+kind already exists.
   - An LLM variant (future) calls the AI router to propose entities from
     unstructured analysis. Both write the same ``entity_candidates`` row
     shape; the analyst can't tell which produced it.
2. **Review UI**: the "Operational entities" tab in AdminScreen
   ([OperationalEntitiesAdmin.tsx](../../frontend/src/components/admin/OperationalEntitiesAdmin.tsx))
   lists pending candidates with proposed name, score, kind, and reason.
3. **Approval** (`POST /api/operational-entity-candidates/{id}/approve`)
   creates the ``operational_entities`` row, projects to Neo4j (with the
   secondary ``:Asset`` label for Vessel/Aircraft/Vehicle), and marks the
   candidate ``status='approved'`` with ``approved_entity_id`` pointing at
   the new row.
4. **Rejection** (`POST /api/operational-entity-candidates/{id}/reject`)
   marks the candidate dismissed.

The same pattern is used for ``:POSSIBLY_SAME_AS`` (Phase 4.E): the
weekly ``worker.tick_entity_resimilarity`` task proposes candidate identity
merges between operational entities; the analyst approves via
`POST /api/operational-entities/{id}/same-as/{other_id}` which writes the
canonical ``:SAME_AS`` edge and deletes the candidate.

## Trade-offs accepted

- **Analyst toil during ramp-up**: every proposal needs a click. We accept
  this because the alternative is undoing bad merges later, which is much
  worse.
- **Two-stage write**: ``entity_candidates`` row → ``operational_entities``
  row on approval is more steps than a direct insert. The cost is
  one-time-per-entity; the queue lets a defence analyst batch-review without
  the entity ever appearing in the live graph until they approve.
- **The heuristic proposer is intentionally weak**. Even when the
  embedding/LLM upgrade lands, the review gate stays.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 4 section.
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md) — the broader
  "never silently mutate identity" principle.
- [decisions/why-candidate-edges-persisted.md](why-candidate-edges-persisted.md)
  — the older candidate-edge pattern this mirrors.
- [backend-routers/operational-entities-router.md](../backend-routers/operational-entities-router.md)
- [backend/graph-writes.md](../backend/graph-writes.md) — `merge_same_as`,
  `merge_possibly_same_as_batch`, `merge_operational_entity`.
