# Merged `additions.responsive.css` Into `index.css`

## Decision

**Removed:** `frontend/src/additions.responsive.css` (separate container-query layer).
**Replacement:** all of its rules now live in `frontend/src/index.css` — one stylesheet.

## Why

- **The two files had drifted into duplicate, conflicting rules.** Both defined
  `.shell-jump-label`, `.shell-context-line`, `.analyst-chip-role/-name`,
  `.imagery-job-*`, `.login-layout`, etc. — at different breakpoints and under
  different container scopes (`additions` used `@container shell-topbar/...`,
  `index.css` used `@container workspace`).
- **The cascade intent was already broken.** `additions.responsive.css` was
  `@import`-ed at the *top* of `index.css` (the CSS spec forces `@import` before
  all rules), so its comment "import last so these take precedence" never held —
  `index.css` already won every tie.
- **Single source of truth.** Responsive behavior split across two files is hard
  to reason about and silently drifts. One file removes that class of bug.

## How it was merged

- Topbar/statusbar collapse rules were folded into `@container workspace (...)`
  blocks. `.shell-body` is the `workspace` container and the topbar/statusbar are
  full-width children of it, so one scope drives every field — the finer
  `shell-topbar`/`shell-statusbar` container names were dropped.
- Genuinely unique rules (ObjectDetailsForm chrome, `@container` blocks for
  `object-details`, `layer-panel`, `selection-panel`, `gaiamap`, `change-dialog`,
  `admin`, `jobs-list`, `alerts-list`, `models-table`, and the reduced-motion
  query) were moved verbatim — these were *not* duplicated in `index.css`.
- Redundant rules (the `:root` token block, `.shell-grid`/`.shell-aside` sizing,
  `.login-*` base layout) were dropped; `index.css` already defined equivalents.

## Trade-offs accepted

- Topbar/statusbar fields now collapse on the **workspace** width rather than the
  individual topbar/statusbar width. For the shell this is equivalent (those rows
  are full-width children of the workspace container); resizable panels keep
  their own component-scoped container names.

## Cross-references

- [shell-and-chrome.md](../frontend/shell-and-chrome.md)
- [object-details-form.md](../frontend/object-details-form.md)
