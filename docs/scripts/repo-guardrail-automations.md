# Repo Guardrail Automations — drift gates + write guard

**Paths:**
- [scripts/repo_guardrails.py](../../scripts/repo_guardrails.py)
- [scripts/stale_terms_audit.py](../../scripts/stale_terms_audit.py)
- [scripts/forbidden_paths_guard.py](../../scripts/forbidden_paths_guard.py)

**Depends on:** Python stdlib only, [scripts/docs_audit.py](../../scripts/docs_audit.py), [agent-entry.md](../agent-entry.md) (Hard Rules + stale-term warnings)

## Purpose

Enforce the conventions in [agent-entry.md](../agent-entry.md) automatically instead
of relying on an agent or author to remember them. Three stdlib-only scripts back
the [Claude Code hooks](../../.claude/settings.json) and the local `.git/hooks/pre-push`
hook.

## Why this design

The repo's docs-discipline workflow, the "no writing baked/runtime artifacts" Hard
Rules, and the recurring stale-model drift are all currently enforced by convention
only. `docs_audit.py` already gates index/link/route structure but is blind to
removed-model prose drift, and nothing stops a write to a baked-artifact path.
These guards close those gaps with zero new dependencies (air-gap safe).

## Key symbols

- [`stale_terms_audit.audit()`](../../scripts/stale_terms_audit.py) — flags removed models (Prithvi, Grounding-DINO, LAE-DINO, RemoteCLIP, SAM3 AMG, FAIR1M-OBB detector) in `README.md` + `docs/**/*.md` **only** when the line lacks removal/historical language, so a "was removed" note stays clean. Allowlists `docs/decisions/`, `docs/archive/`, `docs/benchmarks/`, and `agent-entry.md`; per-line escape hatch is the marker `stale-term-ok`. The bare term `FAIR1M` is never matched — only the removed `FAIR1M-OBB` detector.
- [`forbidden_paths_guard.banned_reason()`](../../scripts/forbidden_paths_guard.py) — PreToolUse guard; blocks Edit/Write/MultiEdit/NotebookEdit to `/data/*`, `bench/`, `assets/static/basemap/`, `inference-sam3/yolo*.pt`, `inference-sam3/yoloe-*.pt`, and `inference-sam3/mobileclip2_b.ts` (exit 2 = deny).
- [`repo_guardrails.run_gates()`](../../scripts/repo_guardrails.py) — runs `docs_audit.py` + `stale_terms_audit.py` and aggregates their verdicts for the Stop hook and the pre-push hook.

## Inputs / Outputs

- `python3 scripts/repo_guardrails.py --check` — run both doc gates, exit 1 on drift
  (used by `.git/hooks/pre-push`).
- `python3 scripts/repo_guardrails.py --stop-hook` — same gates; emit a Claude Code
  `systemMessage` JSON on drift, always exit 0 (used by the `Stop` hook).
- `python3 scripts/stale_terms_audit.py [--list]` — stale-term gate standalone.
- `python3 scripts/forbidden_paths_guard.py --path <p>` — self-test the write guard;
  in a hook it reads the tool-call JSON on stdin.

## Wiring

- [.claude/settings.json](../../.claude/settings.json) — `PreToolUse` → write guard;
  `Stop` → `repo_guardrails --stop-hook`. Committed, so it applies for every agent.
- `.git/hooks/pre-push` — runs `repo_guardrails --check` before the Git LFS step.
  Machine-local (`.git/hooks` is not version-controlled); recreate it per clone.

## Failure modes

- The stale-term context heuristic is line-local: a removed term whose removal note
  is on an adjacent line is flagged — add `stale-term-ok` or allowlist the file.
- The write guard fails open on malformed/empty hook stdin so it can never wedge a
  session; the pre-push gate is the hard backstop.

## Cross-references

- [agent-entry.md](../agent-entry.md)
- [conventions/documentation-workflow.md](../conventions/documentation-workflow.md)
- [scripts/docs-audit.md](docs-audit.md)
