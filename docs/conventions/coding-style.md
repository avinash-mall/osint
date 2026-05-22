# Coding Style

## Python

- **Type hints everywhere** the function shape matters. Pydantic shapes in [backend/schemas.py](../../backend/schemas.py); module-level functions use `from __future__ import annotations` plus PEP 604 `X | Y`.
- **No premature abstraction** — three similar lines beats a clever helper. See [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md) — the monolith is the reigning example.
- **No defensive validation for internal calls** — validate at HTTP boundaries (`schemas.py` Pydantic models). Trust everything else.
- **No spurious comments** — don't restate what the code does. Annotate only the non-obvious *why* (a workaround, a constraint, a past incident). The codebase has very few comments by design.
- **Errors** — specific HTTP codes: 400 (client malformed), 401 (no session), 403 (no admin), 415 (unsupported media), 422 (validation), 503 (downstream down). See [error-handling.md](error-handling.md).

## TypeScript / React

- **One component per file** — some are large (FmvPlayer ~2300 lines), but each file is one workspace's worth of UI. Splitting just for length is not a goal.
- **No Redux / Zustand** — state colocated in the component that owns it. Shared cross-workspace state (cursor lat/lng, current workspace, session user) lives in [Shell.tsx](../../frontend/src/components/Shell.tsx) and `AuthProvider`.
- **Tailwind utility classes** + a few global styles in [index.css](../../frontend/src/index.css). No CSS-in-JS framework.
- **`lucide-react`** is the icon library; custom symbols in [iconLibrary.tsx](../../frontend/src/utils/iconLibrary.tsx).
- **Hooks own data fetching** — components consume hooks. Don't `fetch()` directly inside a component if a hook already wraps the same endpoint.

## Naming

See [naming-and-paths.md](naming-and-paths.md).

## File size

Large is allowed when the file is a single coherent unit (a router, a workspace, the `worker_legacy.py` monolith). Don't split arbitrarily — but a file doing *unrelated* things is a sign the boundaries are wrong.

## Cross-references

- [documentation-workflow.md](documentation-workflow.md) — read-before / update-after rule
- [naming-and-paths.md](naming-and-paths.md)
- [error-handling.md](error-handling.md)
- [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md)
