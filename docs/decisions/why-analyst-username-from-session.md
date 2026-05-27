**Decision:** Plan D's approve/reject endpoints (and `identify`) capture `reviewed_by` from `Depends(get_current_user).username`, NOT from a request-body `analyst` field. This deviates from the existing `detection_target_candidates` pattern at `backend/main.py` (~lines 1988-2073), which reads the analyst name from the request body.

## Why
- **The session cookie is the source of truth for who's logged in.** Trusting a request-body `analyst` field means a malicious or buggy client could submit any name. The session-derived username is signed and cannot be spoofed without an active session.
- **`detection_target_candidates`'s pattern predates the project's auth posture being analyst-centric.** Plan D is the right opportunity to establish the better convention for new code; the old pattern can be migrated later as a separate hygiene task.
- **Simpler clients.** Frontend code in Plan E does not need to fetch and pass `user.username` — it just POSTs and the backend resolves the username from the cookie.

## What we rejected
- **Request-body `analyst` field** — matches the existing pattern but encodes user identity client-side. Rejected for the reason above.
- **A separate audit log table** — premature. The candidate row's `reviewed_by`/`reviewed_at` columns are sufficient for now; a richer audit trail can be added if compliance requirements escalate.

## Consequences
- Plan D's approve/reject routes require `Depends(get_current_user)` explicitly.
- `identify_detection` also takes the dependency for consistency and defense-in-depth, even though it doesn't write any audit columns.
- The session must be valid; a 401 is returned otherwise (handled by the existing middleware on POSTs and by the dependency directly on GETs).
- The decision is decoupled from the existing `detection_target_candidates` pattern, which keeps its current behaviour. A future migration could harmonise.
