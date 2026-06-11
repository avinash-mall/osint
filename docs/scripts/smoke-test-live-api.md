# Live API Smoke Test

**Path:** [scripts/smoke_test_api.py](../../scripts/smoke_test_api.py)
**Lines:** ~960
**Depends on:** a running stack (nginx `:3000`), `requests`, repo-root `.env`
(`ADMIN_USERNAME`/`ADMIN_PASSWORD`), `sample/austin1.tif`, optional `sample/Day Flight.mpg`

## Purpose

End-to-end exercise of the real HTTP API against the running Docker stack — not a
unit test. Logs in as the env admin, then drives every route group: all GET reads,
the mutating CRUD flows (AOI / ontology branch+object / operational entity /
repeat-thresholds / prompt-profiles / confidence-overrides), the manual-detection
lifecycle (create → details/review/tag/identify/candidate-links/target-package →
delete), graph read-POSTs, analytics (viewshed/LOS/routes), and the heavy
jobs/model paths (ingest upload + job poll, inference load/unload). Scores coverage
against an embedded catalog of all 164 routes and reports drift.

## Why this design

- **Catalog-driven coverage, not blind iteration.** `CATALOG` is generated from the
  live OpenAPI spec (`GET /openapi.json` *inside* the backend container — nginx does
  not proxy `/openapi.json` to the host). The run reports `covered/164` and flags any
  endpoint hit that is not in the catalog (spec drift).
- **Tagged + torn-down mutations.** Every created row is prefixed `SMOKE_TEST_` and
  deleted in a `finally`; thresholds/prompt-profiles capture and restore the prior
  *current* row so live detection policy is unchanged.
- **Idle-gated, self-restoring inference.** load/unload only runs when
  `/api/inference/dashboard active_requests == 0` — unloading mid-detection corrupts
  concurrent jobs (the model bundle becomes `None`) and load/unload returns 409 while
  a request is in flight. When busy it SKIPs; afterwards it always reloads the
  captured baseline profile. See [why-live-test-tier.md](../decisions/why-live-test-tier.md).
- **Report-not-fail on environmental conditions.** Missing fixtures (empty DB),
  GPU-job timeouts, an offline LLM (`503` on `ai/extract`), and a busy inference
  service are SKIPs, never FAILs — so the suite stays deterministic and a non-zero
  exit always means a real regression.

## Key symbols

- `CATALOG` [smoke_test_api.py#L39-L204](../../scripts/smoke_test_api.py#L39-L204) — all 164 `(method, path)` pairs.
- `record()` / `skip()` / `hit()` [#L204-L250](../../scripts/smoke_test_api.py#L204-L250) — call + PASS/FAIL/SKIP bookkeeping and coverage tracking.
- `read_tier()` + `PARAM_GETS` — param-free + fixture-resolved path-param GETs.
- `flow_*()` — one function per mutating area; `flow_inference()` + `_inference_idle()` + `restore_inference()` for the model paths.
- `report()` — console table + `scripts/smoke_test_report.json` (summary, coverage, uncovered list, drift, per-endpoint rows).

## Inputs / Outputs

- **In:** `--base` (default `http://localhost:3000`), `--env`, `--json`, `--skip-jobs`,
  `--skip-inference`, `--fmv` (opt-in heavy video upload).
- **Out:** colourised console table, `scripts/smoke_test_report.json`, exit 1 if any
  non-skipped check failed.

Typical default run: **~121 PASS / 0 FAIL / ~16 SKIP, 106/154 covered.** Add `--fmv`
to also exercise the FMV clip + fmv-detection endpoints (pushes coverage to ~108).

## Failure modes

- **FMV (`--fmv`) monopolises the single inference service for minutes** — the
  `worker.process_fmv` video pipeline holds the GPU, so subsequent ingest/inference
  calls see 409/502 until it drains. Default-off for this reason; run it deliberately.
- Login failure (bad `.env` creds) aborts the run.
- A failed teardown leaves `SMOKE_TEST_`-tagged rows; the report calls it out.
- Ingest/FMV artifacts are now torn down: `flow_cleanup` deletes each created pass
  (`DELETE /api/imagery/{id}`) and clip (`DELETE /api/fmv/clips/{id}`) at the end of the
  run, so the corpus no longer grows per run.

## Cross-references

- [decisions/why-live-test-tier.md](../decisions/why-live-test-tier.md)
- [testing/live-e2e-playwright.md](../testing/live-e2e-playwright.md) — the UI half of the live test
- [backend/api-routes-reference.md](../backend/api-routes-reference.md)
- [backend-routers/auth-router.md](../backend-routers/auth-router.md) — login/session contract reused here
