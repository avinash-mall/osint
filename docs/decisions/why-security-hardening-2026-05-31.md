# Why 2026-05-31 Security Hardening Tightened Defaults

**Date:** 2026-05-31
**Status:** Accepted

## Context

A principal security audit found several places where development convenience leaked into deployable defaults: a committed token placeholder, Compose fallback auth secrets, worker-side HTTP(S) imagery fetches, unbounded multipart upload streams, reviewer identity supplied by the browser, permissive backend CORS, unbounded FMV subprocesses, and an optional host-network LLM proxy bound to all interfaces.

## Decision

Sentinel now fails closed for these surfaces:

- `.env.example` contains no real token or generated auth secret.
- `docker-compose.yml` requires `ADMIN_PASSWORD` and `SESSION_SECRET` to be set before the backend starts.
- Remote imagery URLs are disabled unless `ALLOW_REMOTE_IMAGERY_URLS=1`; enabled fetches must pass host allowlisting/public-IP checks and a byte cap.
- Multipart uploads share one `MAX_UPLOAD_BYTES` cap in `backend/files.py`.
- Detection-target candidate approve/reject/promote writes `reviewed_by` from `SessionUser.username` and rejects stale/non-pending rows with 409.
- Operational-entity creation/review/link/merge attribution uses `SessionUser.username` instead of request-body analyst fields.
- Graph dissent (`/api/graph/contradict`) writes the `CONTRADICTED_BY.analyst` property from `SessionUser.username`, not from the body.
- Backend CORS uses `CORS_ORIGINS`.
- FMV `ffprobe`/`ffmpeg` calls have timeouts.
- The optional `llm-local-proxy` profile binds to `127.0.0.1`.

## Consequences

Fresh deployments must provide real `ADMIN_PASSWORD` and `SESSION_SECRET` values in `.env`. Air-gapped runtime remains local-file-first; connected hosts that intentionally ingest remote imagery must opt in and, ideally, allowlist source hosts. Stale analyst review actions now surface conflicts instead of overwriting audit fields.

## Cross-references

- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [deployment/docker-compose-services.md](../deployment/docker-compose-services.md)
- [backend/files-and-uploads.md](../backend/files-and-uploads.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
- [backend-routers/graph-router.md](../backend-routers/graph-router.md)
