# Why Global Mutators Require Admin Sessions

**Date:** 2026-05-27
**Status:** Accepted

## Context

Sentinel has a broad session middleware that blocks unauthenticated mutating verbs, but several global configuration surfaces only depended on "any logged-in user". That let analyst sessions change ontology branches/objects, assign unknown labels, tune repeat-detection thresholds, promote models, queue training jobs, and load/unload inference profiles.

These operations affect every operator and sometimes change worker/inference behavior outside the current browser session.

## Decision

Global mutators now require `require_admin` in the backend. The frontend mirrors that contract by hiding Admin navigation, Admin command-palette actions, and the Admin alerts bell for non-admin sessions.

Read-only operational surfaces remain available according to their existing route contracts. The admin check is scoped to global configuration/model/lifecycle changes, not ordinary analyst review workflows.

## Consequences

- Analyst sessions receive 403 for direct calls to global mutators even when they have a valid session cookie.
- Admin workspace controls no longer appear for analysts, avoiding UI paths that can only fail.
- Tests that exercise admin configuration routes must provide an admin session cookie.

## Cross-references

- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
- [backend-routers/admin-thresholds-router.md](../backend-routers/admin-thresholds-router.md)
- [backend-routers/inference-router.md](../backend-routers/inference-router.md)
- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [frontend/app-and-routing.md](../frontend/app-and-routing.md)
- [frontend/shell-and-chrome.md](../frontend/shell-and-chrome.md)
