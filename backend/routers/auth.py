"""Auth routes: env-admin / LDAP login, session cookie, admin LDAP config."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from auth import (
    LDAPSettings,
    SessionUser,
    authenticate_admin,
    authenticate_ldap,
    cookie_kwargs,
    create_session_cookie,
    get_optional_user,
    load_auth_config,
    require_admin,
    save_auth_config,
    test_ldap_connection,
)
from database import postgis_db
from platform_schema import ensure_platform_tables
from schemas import AuthTestRequest, LoginRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _set_session_cookie(response: Response, user: SessionUser) -> None:
    response.set_cookie(value=create_session_cookie(user), **cookie_kwargs())


def _clear_session_cookie(response: Response) -> None:
    kwargs = cookie_kwargs()
    response.delete_cookie(key=kwargs["key"], path=kwargs["path"])


@router.post("/api/auth/login")
def login(body: LoginRequest, response: Response):
    """Authenticate against the env admin first, then LDAP if configured.

    On success, sets the ``sentinel_session`` cookie and returns the user.
    """
    ensure_platform_tables()
    user = authenticate_admin(body.username, body.password)
    if user is None:
        try:
            cfg = load_auth_config(postgis_db)
        except Exception as exc:
            logger.warning("auth_config load failed: %s", exc)
            cfg = LDAPSettings()
        if cfg.enabled:
            try:
                user = authenticate_ldap(cfg, body.username, body.password)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=f"LDAP: {exc}") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    _set_session_cookie(response, user)
    return {"user": user.to_public(), "role": user.role}


@router.post("/api/auth/logout")
def logout(response: Response):
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request):
    user = get_optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return {"user": user.to_public(), "role": user.role}


@router.get("/api/admin/auth/config")
def admin_auth_get(user: SessionUser = Depends(require_admin)):
    """Return the saved LDAP configuration. ``bind_password`` is masked."""
    ensure_platform_tables()
    cfg = load_auth_config(postgis_db)
    payload = cfg.model_dump() if hasattr(cfg, "model_dump") else json.loads(cfg.json())
    if payload.get("bind_password"):
        payload["bind_password"] = "********"
    return payload


@router.put("/api/admin/auth/config")
def admin_auth_put(cfg: LDAPSettings, user: SessionUser = Depends(require_admin)):
    """Save new LDAP config. If ``bind_password`` is the mask, preserve the existing one."""
    ensure_platform_tables()
    current = load_auth_config(postgis_db)
    if cfg.bind_password == "********":
        cfg.bind_password = current.bind_password
    save_auth_config(postgis_db, cfg, updated_by=user.username)
    test = test_ldap_connection(cfg) if cfg.enabled and cfg.host else {"ok": True, "skipped": True}
    out = cfg.model_dump() if hasattr(cfg, "model_dump") else json.loads(cfg.json())
    if out.get("bind_password"):
        out["bind_password"] = "********"
    return {"config": out, "test": test}


@router.post("/api/admin/auth/test")
def admin_auth_test(body: AuthTestRequest, user: SessionUser = Depends(require_admin)):
    """Test a username/password against the saved LDAP config without storing a session."""
    ensure_platform_tables()
    cfg = load_auth_config(postgis_db)
    if not cfg.enabled:
        return {"ok": False, "error": "LDAP is disabled. Enable it and Save before testing."}
    try:
        result = authenticate_ldap(cfg, body.username, body.password)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    if result is None:
        return {"ok": False, "error": "bind succeeded but credentials were rejected"}
    return {"ok": True, "user": result.to_public()}


@router.post("/api/admin/auth/test-connection")
def admin_auth_test_connection(cfg: LDAPSettings, user: SessionUser = Depends(require_admin)):
    """Run a service-bind smoke test against an *unsaved* config payload."""
    if cfg.bind_password == "********":
        current = load_auth_config(postgis_db)
        cfg.bind_password = current.bind_password
    return test_ldap_connection(cfg)
