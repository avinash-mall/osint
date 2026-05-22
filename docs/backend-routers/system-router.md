# System Router (`/api/system/*`)

**Path:** [backend/routers/system.py](../../backend/routers/system.py)
**Lines:** ~45
**Depends on:** `fastapi`, env (`SENTINEL_DEPLOYMENT_MODE`, `SENTINEL_DEPLOYMENT_LABEL`, `SENTINEL_AUTH_SUPPORT_CONTACT`)

## Purpose

System-metadata routes for the frontend chrome. Currently one endpoint: the deployment-mode banner shown on the login screen.

## Why this design

Login screen used to hardcode a `UNCLASSIFIED // FOR OFFICIAL USE ONLY` classification bar (UX-AUDIT F1). A stock open-source clone cannot back that framing → posture now env-driven, defaults to `demo`; operators opt in to a gov/mil banner. Route intentionally unauthenticated — banner renders before sign-in. It is a GET → session middleware in `main.py` does not gate it.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/system/deployment-mode` | [system.py#L29](../../backend/routers/system.py#L29) | `{mode, label, support_contact}` |

## Inputs / Outputs

- `SENTINEL_DEPLOYMENT_MODE` → `mode`: `demo` (default) \| `internal` \| `accredited`; unrecognised values → `demo`.
- `SENTINEL_DEPLOYMENT_LABEL` → overrides `label`; otherwise per-mode default.
- `SENTINEL_AUTH_SUPPORT_CONTACT` → `support_contact` (or `null`) for the LDAP support line on the login screen.

## Failure modes

- None — pure env read, no I/O. Unset vars resolve to the `demo` default.

## Cross-references

- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)
- [frontend/app-and-routing.md](../frontend/app-and-routing.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
