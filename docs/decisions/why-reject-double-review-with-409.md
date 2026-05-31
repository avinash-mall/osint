# Why approve/reject returns 409 on a non-pending candidate

**Decision:** Candidate review endpoints reject the operation with HTTP 409 (Conflict) when the target candidate is no longer in status `pending`. This covers [`POST /api/identification-candidates/{id}/approve`](../backend-routers/reference-platforms-router.md), detection-target candidate approve/reject, and graph-side candidate-edge promotion. They do NOT idempotently no-op, and they do NOT overwrite the prior reviewer.

**Date:** 2026-05-27.

## Context

Plan D shipped without a `WHERE status='pending'` guard on the UPDATE. Two analysts looking at the same detection's candidate list could race: A approves rank-1, B (with stale state) approves rank-1 a second later, B's `reviewed_by` overwrites A's. End-to-end verification confirmed the race is real (the second approve returned 200 and silently replaced `reviewed_at`).

Three options were considered:

1. **409 Conflict with current state.** Surface the loser's loss; let the UI show "already reviewed by X".
2. **200 idempotent.** Return 200 with the existing row when the target state matches; only 409 on state mismatch.
3. **Force overwrite (status quo).** Last writer wins.

## Why 409

- **Audit-trail integrity beats UX smoothness.** `platform_identification_candidates.reviewed_by` is the canonical record of who decided. Silent overwrite destroys that history; the second analyst's click looks identical to a unilateral decision after the fact. 409 keeps the first decision authoritative.
- **The race is rare.** Two analysts on the same candidate within the WS-event flight time (~1s) is the only realistic trigger. The cost of one 409 + manual refresh is small; the cost of an obscured audit trail is large.
- **The UI can degrade gracefully.** [IdentificationPanel](../frontend/identification-panel.md) detects 409 by inspecting `err.response.data.detail.status`, renders "Already {status} by {reviewer}", and re-runs the candidate fetch. No deadlock, no manual reload required.
- **Symmetric across approve/reject.** Same code path, same response shape — no special-case logic for "approve an already-approved row" vs "reject an already-rejected row".

## How to apply

- The guarded UPDATE pattern lives in [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py) and [backend/main.py](../../backend/main.py): `WHERE id = %s AND status = 'pending'`, followed by a `RETURNING ...` clause. When `cur.fetchone()` returns `None`, a helper disambiguates 404 from already-reviewed 409 with one extra SELECT.
- WebSocket `identification_approved`/`identification_rejected` events fire only after the UPDATE produces a row. On 409, no event is published — clients of the affected detection page learn about the prior reviewer via their normal candidate refresh after the 409 lands.

## Out of scope

- **Cross-rank race.** Analyst A approves rank-1 then analyst B approves rank-2 of the same detection. Both succeed; `object_details.platform_name` reflects the last writer. The 409 guard does not cover this case — it only guards same-candidate races. Tracking issue if needed; not blocking.

## Cross-references

- [why-security-hardening-2026-05-31.md](why-security-hardening-2026-05-31.md)
