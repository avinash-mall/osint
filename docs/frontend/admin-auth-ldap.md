# Admin — Auth · LDAP

**Path:** [frontend/src/components/AdminAuthTab.tsx](../../frontend/src/components/AdminAuthTab.tsx)
**Lines:** ~15182 characters

## Purpose

Editable form for the singleton `auth_config` row: LDAP host, port, base DN, bind DN, password, search filter, admin-group DN. Includes two test buttons.

## Test buttons

- **Test connection** → `POST /api/admin/auth/test-connection` — probes TCP/TLS without binding. Returns `{ok, error}` so the UI shows a result.
- **Test credentials** → `POST /api/admin/auth/test` with a username/password — full LDAP bind against the current config.

## Why this lives in the UI

LDAP settings need to be editable without a service restart in multi-user deployments. The `auth_config` row holds the live settings; env vars (`LDAP_DEFAULT_HOST`, etc.) only seed the first-boot defaults.

## Data sources

- `GET /api/admin/auth/config`
- `PUT /api/admin/auth/config`
- `POST /api/admin/auth/test`
- `POST /api/admin/auth/test-connection`

## Cross-references

- [backend-routers/auth-router.md](../backend-routers/auth-router.md)
- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
- [operations/auth-and-ldap-setup.md](../operations/auth-and-ldap-setup.md)
