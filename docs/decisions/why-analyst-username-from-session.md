**Decision:** Analyst-attributed review/dissent/link endpoints capture the actor from `Depends(get_current_user).username`, NOT from a request-body `analyst` field. This now covers the Reference DB identification queue, detection-target candidate approvals/rejections/promotions, operational-entity review/link/merge actions, and graph dissent edges.

## Why
- **The session cookie is the source of truth for who's logged in.** Trusting a request-body `analyst` field means a malicious or buggy client could submit any name. The session-derived username is signed and cannot be spoofed without an active session.
- **`detection_target_candidates`, operational-entity actions, and graph dissent originally predated the analyst-centric auth posture.** The 2026-05-31 hardening pass migrated them to the same convention as the Reference DB queue.
- **Simpler clients.** Frontend code in Plan E does not need to fetch and pass `user.username` — it just POSTs and the backend resolves the username from the cookie.

## What we rejected
- **Request-body `analyst` field** — matches the existing pattern but encodes user identity client-side. Rejected for the reason above.
- **A separate audit log table** — premature. The candidate row's `reviewed_by`/`reviewed_at` columns are sufficient for now; a richer audit trail can be added if compliance requirements escalate.

## Consequences
- Candidate approve/reject/promote, operational-entity attribution, and graph contradict routes require `Depends(get_current_user)` explicitly.
- `identify_detection` also takes the dependency for consistency and defense-in-depth, even though it doesn't write any audit columns.
- The session must be valid; a 401 is returned otherwise (handled by the existing middleware on POSTs and by the dependency directly on GETs).
- Detection-target candidate rows, operational-entity audit fields, `SAME_AS`/track attachment metadata, and `CONTRADICTED_BY` graph edges now share the same session-derived reviewer contract as platform-identification candidates.

## Cross-references

- [why-security-hardening-2026-05-31.md](why-security-hardening-2026-05-31.md)
