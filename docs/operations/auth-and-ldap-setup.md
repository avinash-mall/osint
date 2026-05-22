# Operations — Auth & LDAP Setup

## Env-bootstrap admin (always available)

`.env`:

```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong random>
SESSION_SECRET=$(openssl rand -hex 32)
```

`SESSION_SECRET` must be ≥ 16 chars or the backend refuses to start.

`ADMIN_USERNAME` / `ADMIN_PASSWORD` always work — even after LDAP is configured. Useful for recovery.

## LDAP (multi-tenant)

1. Sign in as the env-bootstrap admin.
2. **Admin → Auth · LDAP** tab.
3. Fill in: host, port, base DN, bind DN, bind password, user search filter, admin-group DN.
4. **Test connection** → checks TCP/TLS reachability.
5. **Test credentials** → tries a real bind with a test username/password.
6. Save. Subsequent logins try env-bootstrap first, then LDAP.

## Cookie defaults

- `HttpOnly`, `SameSite=Lax`
- `max_age = SESSION_TTL_HOURS` (default 12 h)
- `Secure` when `FORCE_HTTPS=1`

## Logout

Logout allowed without auth — a stale cookie can always be cleared via `POST /api/auth/logout`.

## Cross-references

- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
- [backend-routers/auth-router.md](../backend-routers/auth-router.md)
- [frontend/admin-auth-ldap.md](../frontend/admin-auth-ldap.md)
- [frontend/auth-hook.md](../frontend/auth-hook.md)
