# Recipe — Add a New Router

## When this applies

You're adding a coherent new surface area (e.g. `/api/exports/*`, `/api/calibration/*`) with 3+ routes that share helpers and state.

For one-off endpoints, prefer adding them to [backend/main.py](../../backend/main.py) (the bulk-reads pattern).

## Steps

1. **Create `backend/routers/<name>.py`:**

   ```python
   from __future__ import annotations
   from fastapi import APIRouter, HTTPException, Depends
   from auth import SessionUser, get_current_user, require_admin
   from database import postgis_db
   from schemas import MyNewBody    # define here, see step 2

   router = APIRouter(prefix="/api/<name>", tags=["<name>"])

   @router.get("")
   def list_things():
       ...

   @router.post("", status_code=201)
   def create_thing(body: MyNewBody, user: SessionUser = Depends(get_current_user)):
       ...
   ```

2. **Add shapes to [backend/schemas.py](../../backend/schemas.py)** — do **not** define Pydantic models inside the router file.

3. **Add migrations to [backend/platform_schema.py](../../backend/platform_schema.py)** if new tables are needed:

   ```python
   def ensure_<name>_tables() -> None:
       with postgis_db.cursor() as cur:
           acquire_schema_xact_lock(cur, "sentinel_<name>")
           cur.execute("CREATE TABLE IF NOT EXISTS ...")
   ```

   Then call `ensure_<name>_tables()` from `ensure_platform_tables()`.

4. **Mount in [backend/main.py](../../backend/main.py):**

   ```python
   from routers import <name> as _<name>_router
   app.include_router(_<name>_router.router)
   ```

   (Adjacent to the existing block at [#L170-L182](../../backend/main.py#L170-L182).)

5. **Auth is automatic.** The session middleware at [main.py#L84](../../backend/main.py#L84) gates mutating verbs across all routers. You only need `Depends(get_current_user)` if you want the `SessionUser` object inside the handler.

6. **Write a router doc** at `docs/backend-routers/<name>-router.md` following the template (see existing routers).

7. **Update [docs/INDEX.txt](../INDEX.txt)** and [backend/api-routes-reference.md](../backend/api-routes-reference.md).

## Verifying

- `curl -b "sentinel_session=$COOKIE" http://localhost:3000/api/<name>/...` should respond.
- Unauthenticated `POST` should return 401.

## Cross-references

- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
- [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)
- [backend/platform-schema-migrations.md](../backend/platform-schema-migrations.md)
- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
