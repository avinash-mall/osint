# Decision — AI "brief this AOI" is read-only; map control is an event channel

## Context

ShadowBroker's agent channel can place pins, set the map view, and inject layers
— a write-capable display queue. Sentinel's AI is deliberately advisory: the LLM
proposes, a human approves/executes (`ai_action_proposals`,
`policy: "human_approval_required"`; see
[why-llm-proposed-entities.md](why-llm-proposed-entities.md)). B1 adds the
transferable, offline-viable idea — a situational AOI brief and the ability for
the assistant to drive the map — without weakening that model.

## Decision

- **`POST /api/ai/brief-area` is strictly read-only** (`policy: "read_only"`). It
  composes a digest from data the analyst already has (detections within a
  radius via `ST_DWithin`, recent `timeline_events`) and optionally narrates it
  with the local LLM. It writes nothing except a timeline breadcrumb.
- **Map control is a one-way display channel, not an agent write.** The brief
  returns `display_actions` (currently just `fly_to`); the frontend honours them
  via a `sentinel:map-control` CustomEvent handler in `GaiaMap`, mirroring the
  existing `sentinel:jump-to-detection` pattern. Only **view-changing** actions
  are honoured — no create/edit/delete.

## Why

- **No new mutation surface.** Briefing is analysis; treating it as read-only
  keeps the entire write path behind the existing approval queue. We explicitly
  do **not** adopt ShadowBroker's tier-gated direct agent writes.
- **Reuse the existing event pattern.** `GaiaMap` already drives the map from
  `sentinel:jump-to-detection`; `sentinel:map-control` is the same shape, so the
  map stays the single owner of view state (`MapHandle.flyTo`).
- **Graceful offline degradation.** If the LLM is down, the digest + display
  actions still return — the narrative is simply `null`. Fully on-prem.
- **Pure, testable core.** `_summarize_detections` / `_build_brief_prompt` are
  DB/LLM-free and unit-tested.

## Consequences

- The assistant can focus the analyst's map but cannot change data through this
  path; any data change still goes through `propose-actions` → approve → execute.
- `display_actions` is intentionally small (`fly_to`); extend it with new
  view-only verbs as needed, but writes must not leak into this channel.

## Cross-references

- [backend-routers/ai-router.md](../backend-routers/ai-router.md)
- [decisions/why-llm-proposed-entities.md](why-llm-proposed-entities.md)
- Tests: [backend/tests/test_ai_brief_area.py](../../backend/tests/test_ai_brief_area.py)
