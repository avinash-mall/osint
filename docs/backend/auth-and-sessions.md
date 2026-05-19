# `backend/auth.py` — Sessions, LDAP, Admin

**Path:** [backend/auth.py](../../backend/auth.py)
**Lines:** ~424
**Depends on:** `itsdangerous` (cookie signing), `ldap3` (optional LDAP), [backend/database.py](../../backend/database.py)

## Purpose

Signed session cookie auth with two identity sources: env-bootstrap admin (`ADMIN_USERNAME`/`ADMIN_PASSWORD`) and optional LDAP (from a `auth_config` PostGIS row).

## Why this design

- **`itsdangerous` not JWT.** Cookies are HMAC-signed but contain no claims an attacker would care about — just `{username, is_admin}`. Adding JWT would buy nothing here and add an algorithm/library choice to maintain.
- **`SESSION_SECRET` minimum 16 chars enforced at import.** The application **refuses to start** without it. This is intentional — accidentally running with an empty secret would forge usable cookies trivially.
- **Env-bootstrap admin always available** so the platform is recoverable even if LDAP is misconfigured. LDAP is opt-in: empty `auth_config` row means "no LDAP."
- **Constant-time username/password check** in `authenticate_admin` to avoid timing-channel disclosure of the admin name.
- **Group-membership admin role** via LDAP `admin_group_dn` setting — any user whose `memberOf` contains that DN gets `is_admin=true`. Single-tenant: pure env-admin.

## Key symbols

- [`SessionUser`](../../backend/auth.py#L68) — the unit returned by `get_current_user`.
- [`LDAPSettings`](../../backend/auth.py#L48) — pydantic model persisted in `auth_config`.
- [`create_session_cookie`](../../backend/auth.py#L131) / [`decode_session_cookie`](../../backend/auth.py#L135).
- [`cookie_kwargs`](../../backend/auth.py#L147) — applies `Secure`/`HttpOnly`/`SameSite=Lax` policy based on env.
- [`ensure_auth_tables`](../../backend/auth.py#L164) — schema bootstrap, idempotent.
- [`authenticate_admin`](../../backend/auth.py#L230) and [`authenticate_ldap`](../../backend/auth.py#L248).
- [`require_admin`](../../backend/auth.py) — FastAPI dependency for admin-only routes.

## Failure modes

- Empty/short `SESSION_SECRET` → `RuntimeError` at import.
- LDAP TCP/TLS failure → `authenticate_ldap` returns `None`; auth router returns 401.
- Stale cookie (TTL expired) → `decode_session_cookie` returns `None`.

## Cross-references

- [backend-routers/auth-router.md](../backend-routers/auth-router.md)
- [operations/auth-and-ldap-setup.md](../operations/auth-and-ldap-setup.md)
- Tests: [backend/tests/test_auth.py](../../backend/tests/test_auth.py)
