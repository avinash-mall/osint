# Why the LLM Proposer Falls Back to a Heuristic

## Decision

Phase 5.I upgraded `worker.tick_propose_entities` to call the OpenAI-compatible
LLM (via [backend/ai.py](../../backend/ai.py)) for operational-entity
proposals. The original REPEATED_AT-cluster heuristic stayed in place as a
fallback path. The task tries the LLM first, catches `AIUnavailable`, and
falls through to the heuristic — both branches feed the same
`entity_candidates` review queue.

## Why not remove the heuristic

Three reasons:

1. **Air-gap deployments**: per [decisions/why-postgis-and-neo4j-coexist.md](why-postgis-and-neo4j-coexist.md)
   the project ships offline. [`.env.offline.example`](../../.env.offline.example)
   sets `OPENAI_API_BASE=""` to cleanly disable LLM features. Without the
   heuristic fallback, air-gapped operators would lose entity proposals
   entirely. The heuristic is cheap (pure-Python class-name mapping +
   REPEATED_AT cluster walk) and produces reasonable suggestions when the
   LLM can't answer.

2. **Graceful degradation**: a temporarily unreachable LLM endpoint (proxy
   hiccup, model swap, prompt-cache cold start) shouldn't stop entity
   review from advancing. The heuristic still produces proposals — just
   noisier — so the analyst queue keeps draining.

3. **A/B sanity check**: when both paths run on the same REPEATED_AT
   clusters, an analyst comparing the two streams gets a free signal on
   whether the LLM is hallucinating. Phase 5 doesn't ship a side-by-side
   compare UI, but the `source` column on `entity_candidates` makes the
   audit trivial.

## How the gate works

The task body is roughly:

```python
proposals = []
source = "heuristic"
try:
    llm_proposals = _llm_propose_entities(clusters)  # may raise AIUnavailable
    if llm_proposals:
        proposals = llm_proposals
        source = "llm"
except Exception:
    pass  # fall through
if not proposals:
    proposals = _heuristic_propose_entities(clusters)
```

The empty-LLM-result case (LLM responded but produced zero valid
proposals after Pydantic-style validation) also falls through to the
heuristic. The return payload carries `source ∈ {"llm", "heuristic"}` so
the analyst can tell which produced any given candidate.

## What the LLM prompt asks for

A single-shot, schema-constrained call: system prompt pins the kinds
(`vessel|aircraft|vehicle|facility|unit`), tells the LLM to be
conservative, and requires return shape
`{"proposals":[{"entity_kind","proposed_name","reason","seed_detection_ids"}]}`.
Cluster payload is capped at 60 rows to fit typical local-LLM token
budgets (gemma, llama 3.x). Temperature 0.0; max_tokens 1200.

## Trade-offs accepted

- **Two proposer code paths to maintain**: the heuristic and the LLM
  validation logic both live in `worker_legacy.py`. We accept the cost
  because the heuristic is small (~30 lines) and stable.
- **Cluster payload size cap**: a deployment with hundreds of
  REPEATED_AT clusters per day won't see every one go through the LLM.
  The heuristic picks up the leftovers. This is intentional — the LLM is
  expensive per token; the heuristic is free.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 5.I.
- [decisions/why-llm-proposed-entities.md](why-llm-proposed-entities.md) — the
  original analyst-asserted-vs-LLM-proposed decision.
- [backend/ai.py](../../backend/ai.py) — the OpenAI-compatible client.
