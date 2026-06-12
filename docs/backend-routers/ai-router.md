# AI Router (`/api/ai/*`, `/api/actions/*`)

**Path:** [backend/routers/ai.py](../../backend/routers/ai.py)
**Lines:** ~540
**Depends on:** [backend/ai.py](../../backend/ai.py), [backend/events.py](../../backend/events.py), [backend/platform_schema.py](../../backend/platform_schema.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Routes that touch an LLM (Ava). All return a graceful "LLM unavailable" error when `OPENAI_API_BASE` is unset — the rest of the app works without it. Every surface is **advisory**: it returns `policy: "human_approval_required"` / `"read_only"` or writes to a review queue — never an auto-applied mutation.

`brief-area` (B1) is a **read-only** AOI situational digest (detections within a radius via `ST_DWithin` + recent timeline events, optionally LLM-narrated). It returns `display_actions` (e.g. `fly_to` the AOI centre) that the map UI runs via the `sentinel:map-control` CustomEvent — an approval-safe, in-app analogue of an agent display queue (no writes, no new mutation surface). See [decisions/why-readonly-ai-brief-and-map-control.md](../decisions/why-readonly-ai-brief-and-map-control.md).

## Endpoints

| Method | Path | Source | Body / params |
|---|---|---|---|
| `POST` | `/api/ai/analyze` | [ai.py#L27](../../backend/routers/ai.py#L27) | `AIAnalysisRequest` — free-text analyst question over selected detections/area |
| `POST` | `/api/ai/extract` | [ai.py#L54](../../backend/routers/ai.py#L54) | LLM extracts structured entities from raw text |
| `POST` | `/api/ai/link` | [ai.py#L127](../../backend/routers/ai.py#L127) | LLM-ranked candidate-target link suggestions for a detection |
| `POST` | `/api/ai/brief-area` | [ai.py#L499](../../backend/routers/ai.py#L499) | `BriefAreaRequest` — **read-only** AOI digest + `display_actions`; pure helpers `_summarize_detections` / `_build_brief_prompt`; `_detections_within` reads `class AS object_class` and excludes soft-deleted rows |
| `POST` | `/api/ai/propose-actions` | [ai.py#L197](../../backend/routers/ai.py#L197) | `AIActionProposalRequest` — LLM suggests next-step analyst actions. Confidence comes from `payload.confidence` (clamped 0..1) when the proposer supplies one, else a 0.62 default; the source is recorded as `confidence_source` (`proposer`/`default`) in the stored payload |
| `GET` | `/api/actions/proposals` | [ai.py#L234](../../backend/routers/ai.py#L234) | List proposal queue |
| `POST` | `/api/actions/proposals/{id}/approve` | [ai.py#L255](../../backend/routers/ai.py#L255) | Operator approves a proposal; records the real `SessionUser.username` as `approved_by` (was a hardcoded `'local_user'`) |
| `POST` | `/api/actions/proposals/{id}/execute` | [ai.py#L307](../../backend/routers/ai.py#L307) | Runs an approved proposal. Claim-first: atomically flips `approved → executing` (409 if not claimable, so a double-click can't run side effects twice), runs `_run_proposal_side_effects`, then finalizes to `executed`; on a side-effect exception the claim is released back to `approved` for retry. A `queue_analytic` action resolves the proposal's `target_id` to an observer via `_resolve_target_observer` (centroid of the target's approved detections) so the viewshed runs AT the target, not at the default observer; skips with a warning if unresolvable |

## Why this design

- LLM = **optional infrastructure**, not core path. Each endpoint catches `AIUnavailable` from [backend/ai.py](../../backend/ai.py) → 503 with stable error shape → frontend shows "LLM offline" without crashing.
- AI suggestions go through **approve-then-execute**, not auto-apply. See [operations/llm-ava-configuration.md](../operations/llm-ava-configuration.md).
- LLM JSON via [`get_llm_json`](../../backend/ai.py) — unit-tested in [backend/tests/test_ai_json_parsing.py](../../backend/tests/test_ai_json_parsing.py); handles fenced/strict/prose-wrapped JSON.

## Failure modes

- `OPENAI_API_BASE` unset → all routes return `{detail: "LLM unavailable"}` 503.
- Malformed LLM JSON → `get_llm_json` retries with a strict-mode prompt before raising.

## Cross-references

- [backend/ai-llm-integration.md](../backend/ai-llm-integration.md) — the underlying client
- [operations/llm-ava-configuration.md](../operations/llm-ava-configuration.md)
- [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)
- [decisions/audit-fixes-api-layer-2026-06-11.md](../decisions/audit-fixes-api-layer-2026-06-11.md) — the 2026-06-11 API-layer audit batch
