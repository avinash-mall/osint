# Auth Router (`/api/auth/*`, `/api/admin/auth/*`)

**Path:** [backend/routers/auth.py](../../backend/routers/auth.py)
**Lines:** ~128
**Depends on:** [backend/auth.py](../../backend/auth.py), [backend/database.py](../../backend/database.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Login, logout, current-user introspection, admin LDAP configuration. **Only** `POST /api/auth/login` and `POST /api/auth/logout` are unauthenticated mutating endpoints, and reads are gated too apart from the public allowlist (`/api/auth/*`, `/api/health`, `/api/system/deployment-mode`, `/api/ontology/default-prompts`) — see [backend/main.py#L107-L142](../../backend/main.py#L107-L142) and [decisions/why-read-routes-require-session.md](../decisions/why-read-routes-require-session.md).

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `POST` | `/api/auth/login` | [auth.py#L41](../../backend/routers/auth.py#L41) | `{username, password}` → sets `sentinel_session` cookie |
| `POST` | `/api/auth/logout` | [auth.py#L66](../../backend/routers/auth.py#L66) | Clears the cookie |
| `GET` | `/api/auth/me` | [auth.py#L72](../../backend/routers/auth.py#L72) | Returns current `SessionUser` |
| `GET` | `/api/admin/auth/config` | [auth.py#L80](../../backend/routers/auth.py#L80) | Read LDAP settings (admin only) |
| `PUT` | `/api/admin/auth/config` | [auth.py#L91](../../backend/routers/auth.py#L91) | Persist LDAP settings (admin only) |
| `POST` | `/api/admin/auth/test` | [auth.py#L106](../../backend/routers/auth.py#L106) | Try username/password bind against current config |
| `POST` | `/api/admin/auth/test-connection` | [auth.py#L122](../../backend/routers/auth.py#L122) | Probe LDAP TCP/TLS connectivity |

## Why this design

- **Login** checks env-bootstrap admin first (`ADMIN_USERNAME`/`ADMIN_PASSWORD`), then LDAP if a valid `auth_config` row exists. Order keeps the platform recoverable from `.env` even if LDAP misbehaves.
- **Admin endpoints separated** by `/api/admin/` prefix, gated by `require_admin` dependency from [backend/auth.py](../../backend/auth.py).
- **LDAP settings live in PostGIS** (`auth_config` singleton row, not env) → editable from the UI without service restart.

## Failure modes

- Bad credentials → 401, stable shape.
- LDAP unreachable on `/test`/`/test-connection` → 200 with `{ok: false, error: ...}` → UI renders the test result without it being an exception.

## Cross-references

- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
- [operations/auth-and-ldap-setup.md](../operations/auth-and-ldap-setup.md)
- [frontend/admin-auth-ldap.md](../frontend/admin-auth-ldap.md)
