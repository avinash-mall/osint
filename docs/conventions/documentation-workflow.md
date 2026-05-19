# Documentation Workflow (for coding agents)

**This is mandatory.** Every agent session in this repo follows the read-before, update-after loop. Documentation is part of the working code — not an afterthought.

## Before starting any task

1. **Read [docs/INDEX.txt](../INDEX.txt) first.** It's a 15 KB compressed index of every doc; a single read gives you the full tree.
2. **Read the docs for the modules you'll touch.** Grep INDEX.txt by tag (`backend`, `inference`, `fmv`, `ontology`, etc.) or by path to find them.
3. **Read the relevant decision docs.** If you're changing inference, read [docs/decisions/why-sam3-as-foundation.md](../decisions/why-sam3-as-foundation.md), `why-yoloe-replaced-amg.md`, etc. Past trade-offs are load-bearing.
4. **Read the relevant convention.** If the task fits a recipe (`adding-a-new-detection-model.md`, `-router.md`, `-celery-task.md`, `-ontology-object.md`, `-admin-tab.md`), follow it — don't re-derive the steps.

## After finishing any task

When code changes, the docs change. **Update before declaring the task done.**

For every file you modified, write or update its module doc to match the new state:

1. **Update line counts** in the `**Lines:** ~NNN` header if the file grew or shrank by >10%.
2. **Update `Key symbols` lists** if you added, removed, or renamed public functions/classes — keep `file.py#Lx-Ly` ranges current.
3. **Update `Depends on:`** if imports changed.
4. **Update `Failure modes`** if you changed error handling or removed a fallback.
5. **Add a decision doc** at `docs/decisions/<name>.md` for any architectural choice or removal you made (even a small one). Link it from `## Cross-references` of the affected module doc. See existing decisions for the shape.
6. **Update [docs/INDEX.txt](../INDEX.txt)** if you added or renamed a doc. One line per doc, sorted by path: `path|tags|one-line-summary`. Tags from the fixed vocabulary in [docs/conventions/](.) — don't invent new tags.
7. **Update cross-references** — every doc the affected module is mentioned from. Grep is your friend: `grep -rn "<module-name>" docs/`.

## The fixed doc shape (mandatory)

```markdown
# <module name>

**Path:** [path/to/file](../../path/to/file)
**Lines:** ~NNN
**Depends on:** module list + env vars

## Purpose
One sentence.

## Why this design
Architectural intent + past incident if any. Reference decision docs.

## Key symbols
- [`function_name(args)`](../../path/to/file#L42-L58) — one line.

## Inputs / Outputs
Schema or signature. Link to schemas.py, never paste types.

## Failure modes
Raise / fallback / log.

## Cross-references
- Related decision: [why-xxx.md](../decisions/why-xxx.md)
- Used by: [other-module.md](other-module.md)
```

Code links use `../../path` (relative from `docs/<section>/`); inter-doc links use relative paths from the doc's own location.

## When the recipe doesn't fit

If your task is genuinely novel (no convention recipe applies):

1. **Still update affected module docs.**
2. **Write a new convention** at `docs/conventions/<name>.md` for whatever pattern you established. Future agents will follow it instead of re-deriving.
3. **Add to INDEX.txt** and link from neighboring docs.

## When code is removed

Removed code → removed doc + add a `docs/decisions/removed-<name>.md` explaining what was removed and why. See [decisions/removed-defence-yolo.md](../decisions/removed-defence-yolo.md), `removed-dinov3-lvd.md`, `removed-sam3-amg.md` for the shape.

## Never

- Never declare a task done without updating the docs for the files you touched.
- Never write docs outside `docs/` (except for the root pointer files [AGENTS.md](../../AGENTS.md), [CLAUDE.md](../../CLAUDE.md), [.cursor/rules](../../.cursor/rules)).
- Never re-paste code into a doc — link to it.
- Never invent a new section heading. Use the fixed six (`Purpose`, `Why this design`, `Key symbols`, `Inputs / Outputs`, `Failure modes`, `Cross-references`).
- Never invent tags for INDEX.txt outside the fixed vocabulary: `arch | backend | inference | frontend | router | deployment | decision | operations | testing | benchmark | scripts | conventions | fmv | imagery | sam3 | ontology | auth | gpu`.

## Verification before declaring done

Quick checks (run them yourself):

```bash
# Every link in your changed docs resolves
grep -rEho '\]\([^)]+\)' docs/<changed-files> | sort -u   # spot-check 5 random ones

# INDEX.txt is in sync with the file tree
find docs -name '*.md' | wc -l    # should equal INDEX.txt line count + 1 (for docs/README.md)
```

## Cross-references

- [docs/README.md](../README.md) — the doc-tree landing page (has the template too)
- [docs/INDEX.txt](../INDEX.txt) — the compressed index
- [AGENTS.md](../../AGENTS.md), [CLAUDE.md](../../CLAUDE.md), [.cursor/rules](../../.cursor/rules) — root pointers that enforce this rule from session start
