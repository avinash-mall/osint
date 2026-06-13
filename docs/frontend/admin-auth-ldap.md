# Admin — Auth · LDAP

**Path:** [frontend/src/components/AdminAuthTab.tsx](../../frontend/src/components/AdminAuthTab.tsx)
**Lines:** ~491

## Purpose

Editable form for the singleton `auth_config` row: LDAP host, port, base DN, bind DN, password, search filter, admin-group DN. Two test buttons.

## Test buttons

- **Test connection** → `POST /api/admin/auth/test-connection` — probes TCP/TLS without binding. Returns `{ok, error}` → UI shows a result.
- **Test credentials** → `POST /api/admin/auth/test` with a username/password — full LDAP bind against the current config.
- Save returns an inline `test` result; when the backend skipped the bind (`{ok:true, skipped:true}` — LDAP disabled or host empty, [backend/routers/auth.py](../../backend/routers/auth.py)) the UI shows "Saved — bind test skipped" instead of claiming a successful bind. Error messages pass through [`apiErrorMessage`](../../frontend/src/utils/apiError.ts).

## Why this lives in the UI

LDAP settings must be editable without service restart in multi-user deployments. The `auth_config` row holds live settings; env vars (`LDAP_DEFAULT_HOST`, etc.) only seed first-boot defaults.

## Data sources

- `GET /api/admin/auth/config`
- `PUT /api/admin/auth/config`
- `POST /api/admin/auth/test`
- `POST /api/admin/auth/test-connection`

## Cross-references

- [backend-routers/auth-router.md](../backend-routers/auth-router.md)
- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
- [operations/auth-and-ldap-setup.md](../operations/auth-and-ldap-setup.md)
