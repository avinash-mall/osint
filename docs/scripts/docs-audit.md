# `scripts/docs_audit.py` — Documentation Drift Audit

**Path:** [scripts/docs_audit.py](../../scripts/docs_audit.py)
**Lines:** ~363
**Depends on:** Python stdlib (`argparse`, `ast`, `re`, `pathlib`), [docs/INDEX.txt](../INDEX.txt), [backend/api-routes-reference.md](../backend/api-routes-reference.md)

## Purpose

Check and regenerate the agent-facing documentation maps: index sync, fixed tag
vocabulary, relative links, line-count drift warnings, and FastAPI route
coverage.

## Why this design

The docs are intentionally cheap for agents to read, but that only works if the
compressed maps stay current. A stdlib-only script can run on air-gapped dev
hosts, extract route decorators without importing the backend, and regenerate the
mechanical files without adding another dependency.

## Key symbols

- [`write_index()`](../../scripts/docs_audit.py#L136) — rewrites `docs/INDEX.txt` from the current doc tree, preserving existing summaries/tags when valid and inferring fixed-vocabulary tags for new docs.
- [`audit_index()`](../../scripts/docs_audit.py#L153) — validates sort order, missing/stale entries, and fixed tags.
- [`audit_links()`](../../scripts/docs_audit.py#L171) — checks relative Markdown links and rejects non-portable `file://` links; fenced examples are ignored.
- [`audit_lines()`](../../scripts/docs_audit.py#L188) — reports `**Lines:**` drift over 10% as warnings.
- [`extract_routes()`](../../scripts/docs_audit.py#L235) — static AST extractor for string-literal FastAPI/WebSocket decorators under `backend/`.
- [`write_route_appendix()`](../../scripts/docs_audit.py#L286) — rewrites [backend/api-routes-appendix.md](../backend/api-routes-appendix.md).

## Inputs / Outputs

- `python3 scripts/docs_audit.py` — read-only audit.
- `python3 scripts/docs_audit.py --write-index` — regenerate [INDEX.txt](../INDEX.txt).
- `python3 scripts/docs_audit.py --write-route-appendix` — regenerate [api-routes-appendix.md](../backend/api-routes-appendix.md).
- `python3 scripts/docs_audit.py --print-routes` — print extracted route triples.

## Failure modes

- Dynamic route paths that are not string literals are skipped; document those
  manually in [backend/api-routes-reference.md](../backend/api-routes-reference.md).
- Line-count drift is a warning by default because legacy docs are still being
  cleaned incrementally.

## Cross-references

- [conventions/documentation-workflow.md](../conventions/documentation-workflow.md)
- [agent-entry.md](../agent-entry.md)
