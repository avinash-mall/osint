"""Authentication: env-bootstrap admin + configurable LDAP + signed cookie sessions.

Layout
------
* ``authenticate_admin``     – constant-time check against ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``.
* ``authenticate_ldap``      – binds against the LDAP config stored in PostGIS' ``auth_config`` row.
* ``create_session_cookie``  – signs a ``{user, role, exp}`` payload with ``SESSION_SECRET`` (itsdangerous).
* ``get_current_user``       – FastAPI dependency; raises 401 on missing/expired/invalid cookie.
* ``require_admin``          – FastAPI dependency that 403s non-admins.
* ``load_auth_config`` /
  ``save_auth_config``       – read/write the singleton row.

Notes
-----
* No JWT — cookies only. Default name ``sentinel_session``, ``HttpOnly``,
  ``SameSite=Lax``, ``Secure`` when ``FORCE_HTTPS=1``.
* LDAP bind tries the user's DN first via search-then-bind; falls back to a
  simple-bind with the user's CN/UID for setups that allow it.
* Admin role is reserved for the env bootstrap user. LDAP users are
  ``analyst`` unless they appear in ``admin_group_dn`` (group membership
  check is optional).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sentinel_session"
DEFAULT_TTL_HOURS = 12

# -----------------------------------------------------------------------------
# Settings model
# -----------------------------------------------------------------------------


class LDAPSettings(BaseModel):
    """Persistable LDAP configuration. Stored as JSON in ``auth_config.config``."""

    enabled: bool = False
    host: str = ""
    port: int = 389
    use_tls: bool = False
    bind_dn: str = ""
    bind_password: str = ""
    user_base_dn: str = ""
    # ``{username}`` is substituted with the typed username at lookup time.
    user_search_filter: str = "(uid={username})"
    attr_username: str = "uid"
    attr_displayname: str = "cn"
    attr_email: str = "mail"
    # Optional. If set, users whose memberOf includes this DN get role=admin.
    admin_group_dn: str = ""


@dataclass
class SessionUser:
    """The shape we round-trip through the cookie."""

    username: str
    role: str  # "admin" | "analyst"
    display_name: str = ""
    email: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            "u": self.username,
            "r": self.role,
            "n": self.display_name,
            "e": self.email,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "SessionUser":
        return cls(
            username=str(payload.get("u") or ""),
            role=str(payload.get("r") or "analyst"),
            display_name=str(payload.get("n") or ""),
            email=str(payload.get("e") or ""),
        )

    def to_public(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "display_name": self.display_name or self.username,
            "email": self.email,
        }


# -----------------------------------------------------------------------------
# Cookie signing
# -----------------------------------------------------------------------------


def _session_secret() -> str:
    secret = os.getenv("SESSION_SECRET", "")
    if not secret or len(secret) < 16:
        # Don't silently fall back to a weak default — that would make all
        # sessions forgeable. Log loud and force the caller to fix .env.
        raise RuntimeError(
            "SESSION_SECRET is missing or too short (<16 chars). "
            "Set it in .env to a long random string (openssl rand -hex 32)."
        )
    return secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt="sentinel-session-v1")


def _ttl_seconds() -> int:
    try:
        hours = int(os.getenv("SESSION_TTL_HOURS", str(DEFAULT_TTL_HOURS)))
    except ValueError:
        hours = DEFAULT_TTL_HOURS
    return max(60, hours * 3600)


def create_session_cookie(user: SessionUser) -> str:
    return _serializer().dumps(user.to_payload())


def decode_session_cookie(token: str) -> Optional[SessionUser]:
    try:
        payload = _serializer().loads(token, max_age=_ttl_seconds())
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    return SessionUser.from_payload(payload)


def cookie_kwargs() -> Dict[str, Any]:
    secure = os.getenv("FORCE_HTTPS", "0") == "1"
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "max_age": _ttl_seconds(),
        "path": "/",
    }


# -----------------------------------------------------------------------------
# Persistent config (singleton row in auth_config)
# -----------------------------------------------------------------------------


def ensure_auth_tables(postgis_db) -> None:
    """Idempotent migration for the auth_config singleton row."""
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('sentinel_auth_schema'))")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auth_config (
                id          INTEGER PRIMARY KEY DEFAULT 1,
                config      JSONB   NOT NULL DEFAULT '{}'::jsonb,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_by  TEXT,
                CHECK (id = 1)
            )
        """)
        # Ensure the singleton row exists with first-boot defaults from env.
        cur.execute("SELECT 1 FROM auth_config WHERE id = 1")
        if cur.fetchone() is None:
            defaults = LDAPSettings(
                enabled=False,
                host=os.getenv("LDAP_DEFAULT_HOST", ""),
                port=int(os.getenv("LDAP_DEFAULT_PORT", "389") or 389),
                bind_dn=os.getenv("LDAP_DEFAULT_BIND_DN", ""),
                user_base_dn=os.getenv("LDAP_DEFAULT_BASE_DN", ""),
            )
            cur.execute(
                "INSERT INTO auth_config (id, config, updated_by) VALUES (1, %s::jsonb, %s)",
                (defaults.model_dump_json(), "bootstrap"),
            )


def load_auth_config(postgis_db) -> LDAPSettings:
    with postgis_db.get_cursor() as cur:
        cur.execute("SELECT config FROM auth_config WHERE id = 1")
        row = cur.fetchone()
    if not row:
        return LDAPSettings()
    raw = row[0] if not isinstance(row, dict) else row.get("config")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return LDAPSettings(**(raw or {}))


def save_auth_config(postgis_db, cfg: LDAPSettings, updated_by: str = "admin") -> LDAPSettings:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('sentinel_auth_schema'))")
        cur.execute(
            """
            INSERT INTO auth_config (id, config, updated_at, updated_by)
            VALUES (1, %s::jsonb, NOW(), %s)
            ON CONFLICT (id) DO UPDATE
              SET config = EXCLUDED.config,
                  updated_at = EXCLUDED.updated_at,
                  updated_by = EXCLUDED.updated_by
            """,
            (cfg.model_dump_json(), updated_by),
        )
    return cfg


# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------


def authenticate_admin(username: str, password: str) -> Optional[SessionUser]:
    """Constant-time check against the env bootstrap admin."""
    expected_user = os.getenv("ADMIN_USERNAME", "")
    expected_pass = os.getenv("ADMIN_PASSWORD", "")
    if not expected_user or not expected_pass:
        return None
    ok_user = secrets.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
    ok_pass = secrets.compare_digest(password.encode("utf-8"), expected_pass.encode("utf-8"))
    if not (ok_user and ok_pass):
        return None
    return SessionUser(
        username=expected_user,
        role="admin",
        display_name=expected_user.title(),
        email="",
    )


def authenticate_ldap(cfg: LDAPSettings, username: str, password: str) -> Optional[SessionUser]:
    """Bind against the configured LDAP. Returns None on auth failure.

    Raises ``RuntimeError`` for connection-level problems so the caller can
    surface a useful error in the login form / Admin · Auth test.
    """
    if not cfg.enabled:
        return None
    if not (cfg.host and cfg.user_base_dn and cfg.user_search_filter):
        raise RuntimeError("LDAP enabled but host / user_base_dn / user_search_filter not set")
    if not username or not password:
        return None

    try:
        from ldap3 import Connection, Server, ALL, Tls
        from ldap3.core.exceptions import LDAPException
        import ssl
    except ImportError as exc:
        raise RuntimeError(f"ldap3 not installed: {exc}") from exc

    tls = Tls(validate=ssl.CERT_REQUIRED) if cfg.use_tls else None
    server = Server(cfg.host, port=cfg.port or 389, use_ssl=cfg.use_tls, tls=tls, get_info=ALL)

    # Step 1: bind with the service account (or anonymous) to look the user up.
    try:
        if cfg.bind_dn:
            search_conn = Connection(
                server, user=cfg.bind_dn, password=cfg.bind_password,
                auto_bind=True, raise_exceptions=True,
            )
        else:
            search_conn = Connection(server, auto_bind=True, raise_exceptions=True)
    except LDAPException as exc:
        raise RuntimeError(f"LDAP service bind failed: {exc}") from exc

    search_filter = cfg.user_search_filter.replace("{username}", _escape_ldap_filter(username))
    attrs = [a for a in {cfg.attr_username, cfg.attr_displayname, cfg.attr_email, "memberOf"} if a]
    try:
        search_conn.search(cfg.user_base_dn, search_filter, attributes=attrs)
    except LDAPException as exc:
        search_conn.unbind()
        raise RuntimeError(f"LDAP search failed: {exc}") from exc

    if not search_conn.entries:
        search_conn.unbind()
        return None

    entry = search_conn.entries[0]
    user_dn = entry.entry_dn
    display_name = _attr_str(entry, cfg.attr_displayname) or username
    email = _attr_str(entry, cfg.attr_email)
    member_of = _attr_list(entry, "memberOf")
    search_conn.unbind()

    # Step 2: re-bind as the resolved user DN with the typed password.
    try:
        user_conn = Connection(server, user=user_dn, password=password, raise_exceptions=False)
        if not user_conn.bind():
            user_conn.unbind()
            return None
        user_conn.unbind()
    except LDAPException as exc:
        raise RuntimeError(f"LDAP user bind failed: {exc}") from exc

    role = "admin" if cfg.admin_group_dn and cfg.admin_group_dn in member_of else "analyst"
    return SessionUser(
        username=username,
        role=role,
        display_name=display_name,
        email=email,
    )


def _escape_ldap_filter(value: str) -> str:
    # Per RFC 4515, escape these characters in filters.
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\5c")
        elif ch == "*":
            out.append("\\2a")
        elif ch == "(":
            out.append("\\28")
        elif ch == ")":
            out.append("\\29")
        elif ch == "\x00":
            out.append("\\00")
        else:
            out.append(ch)
    return "".join(out)


def _attr_str(entry, name: str) -> str:
    if not name:
        return ""
    val = getattr(entry, name, None)
    if val is None:
        return ""
    try:
        return str(val.value) if hasattr(val, "value") else str(val)
    except Exception:
        return ""


def _attr_list(entry, name: str) -> list:
    val = getattr(entry, name, None)
    if val is None:
        return []
    try:
        return [str(x) for x in val.values]
    except Exception:
        return [str(val)]


def test_ldap_connection(cfg: LDAPSettings) -> Dict[str, Any]:
    """Smoke-test an LDAP config without authenticating a user.

    Returns ``{"ok": True}`` on a successful service bind, or ``{"ok": False, "error": "..."}``.
    """
    if not cfg.host:
        return {"ok": False, "error": "host is required"}
    try:
        from ldap3 import Connection, Server, ALL
    except ImportError as exc:
        return {"ok": False, "error": f"ldap3 not installed: {exc}"}

    server = Server(cfg.host, port=cfg.port or 389, use_ssl=cfg.use_tls, get_info=ALL)
    started = time.monotonic()
    try:
        if cfg.bind_dn:
            conn = Connection(
                server, user=cfg.bind_dn, password=cfg.bind_password,
                auto_bind=True, raise_exceptions=True,
            )
        else:
            conn = Connection(server, auto_bind=True, raise_exceptions=True)
        elapsed = round((time.monotonic() - started) * 1000)
        info = {
            "ok": True,
            "rtt_ms": elapsed,
            "server_type": str(getattr(server.info, "vendor_name", "") or "ldap"),
        }
        conn.unbind()
        return info
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# -----------------------------------------------------------------------------
# FastAPI dependencies
# -----------------------------------------------------------------------------


def get_current_user(request: Request) -> SessionUser:
    """Read and validate the session cookie. Raises 401 on failure."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user = decode_session_cookie(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired session")
    return user


def get_optional_user(request: Request) -> Optional[SessionUser]:
    """Returns the session user or None — never raises. For read-only paths
    that want to know who the caller is without requiring auth."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return decode_session_cookie(token)


def require_admin(user: SessionUser = Depends(get_current_user)) -> SessionUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return user
