# `backend/auth.py` — Sessions, LDAP, Admin

**Path:** [backend/auth.py](../../backend/auth.py)
**Lines:** ~424
**Depends on:** `itsdangerous` (cookie signing), `ldap3` (optional LDAP), [backend/database.py](../../backend/database.py)

## Purpose

Signed session-cookie auth, two identity sources: env-bootstrap admin (`ADMIN_USERNAME`/`ADMIN_PASSWORD`) and optional LDAP (from an `auth_config` PostGIS row).

## Why this design

- **`itsdangerous` not JWT** — cookies HMAC-signed, carry only `{username, is_admin}`; JWT would add an algorithm/library to maintain for no gain.
- **`SESSION_SECRET` ≥ 16 chars enforced at import** — app **refuses to start** without it; empty secret would forge usable cookies trivially.
- **Env-bootstrap admin always available** — platform recoverable even if LDAP misconfigured. LDAP opt-in: empty `auth_config` row = no LDAP.
- **Constant-time username/password check** in `authenticate_admin` — avoids timing-channel disclosure of admin name.
- **Group-membership admin role** — LDAP `admin_group_dn` setting; user whose `memberOf` contains that DN gets `is_admin=true`. Single-tenant: pure env-admin.

## Key symbols

- [`SessionUser`](../../backend/auth.py#L68) — unit returned by `get_current_user`.
- [`LDAPSettings`](../../backend/auth.py#L48) — pydantic model persisted in `auth_config`.
- [`create_session_cookie`](../../backend/auth.py#L131) / [`decode_session_cookie`](../../backend/auth.py#L135).
- [`cookie_kwargs`](../../backend/auth.py#L147) — `Secure`/`HttpOnly`/`SameSite=Lax` policy by env.
- [`ensure_auth_tables`](../../backend/auth.py#L164) — idempotent schema bootstrap.
- [`authenticate_admin`](../../backend/auth.py#L230), [`authenticate_ldap`](../../backend/auth.py#L248).
- [`require_admin`](../../backend/auth.py) — FastAPI dependency for admin-only routes.

## Failure modes

- Empty/short `SESSION_SECRET` → `RuntimeError` at import.
- LDAP TCP/TLS failure → `authenticate_ldap` returns `None` → auth router 401.
- Stale cookie (TTL expired) → `decode_session_cookie` returns `None`.

## Cross-references

- [backend-routers/auth-router.md](../backend-routers/auth-router.md)
- [operations/auth-and-ldap-setup.md](../operations/auth-and-ldap-setup.md)
- Tests: [backend/tests/test_auth.py](../../backend/tests/test_auth.py)
