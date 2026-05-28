# `backend/scripts/bake_reference_index.py` — Reference DB Baker

**Path:** [backend/scripts/bake_reference_index.py](../../backend/scripts/bake_reference_index.py)
**Lines:** ~205
**Depends on:** `backend/reference_platform_db.py`, `backend/platform_schema.py`, `requests`, inference-sam3 `:8001/embed` route.

## Purpose
Populates `reference_platforms` and `reference_chips` from a curated seed manifest plus a per-class chip tree on disk. For each chip image, posts to inference-sam3's `/embed`, decodes the fp16 vector, INSERTs it into `reference_chips.embedding_overhead`, and (at the end) recomputes per-platform centroids.

Designed for build-time / long-running-pipeline use — not request-path.

## Auto-seed at lifespan

Operators no longer need to invoke this script manually for a fresh stack. Backend lifespan calls [`auto_enqueue_reference_seed_if_empty`](../../backend/platform_schema.py); when `reference_platforms` has zero rows, it enqueues the `worker.seed_reference_db` Celery task, which iterates every dataset present under `/opt/reference-corpora/` (populated by the `assets` image — see [why-bake-reference-corpora-into-assets.md](../decisions/why-bake-reference-corpora-into-assets.md)) and calls `run()` per dataset. WS progress streams on the `reference-seed` topic; admin "Re-seed" hits `POST /api/admin/reference/seed` to trigger the same path on demand.

Disable auto-trigger with `REFERENCE_DB_AUTO_SEED=0`. The manual recipe below still works for one-off dataset additions outside the assets-image bake.

## Why this design
- **HTTP to inference-sam3** instead of importing `embedding` directly: keeps the bake script in the backend container (where `psycopg2` and the connection pool live) and reuses the already-loaded DINOv3-SAT model in GPU VRAM. See [why-standalone-embed-endpoint.md](../decisions/why-standalone-embed-endpoint.md).
- **Idempotent on `(platform_id, chip_path)`** via the unique index added to `reference_chips`. Re-runs upsert in place rather than duplicate.
- **Centroid recompute scoped per-platform** so the returned count equals the number of seed platforms baked, not a global rowcount that might also touch leftover test rows.
- **Two transactions** (chip inserts in tx 1, centroid recompute in tx 2): any partial chip insert is durable even if the centroid step rolls back. NOTE: the chip-insert side currently uses one transaction across all chips — fine at DOTA scale (~hundreds of chips). For xView/RarePlanes scale (tens of thousands), per-platform commits will be needed; flagged in the source as a `# NOTE:` comment.
- **Fail-loud counters**: `chips_failed` is tracked and emitted in both the log line and the returned JSON; `RuntimeError` raised when `platforms_written > 0 and chips_written == 0` so a misconfigured `--dataset-root` cannot silently produce an empty index.
- **HTTP seam**: `_post_embed(url, files, timeout)` is a module-level function that tests monkey-patch. Single-purpose, no DI framework.

## Key symbols
- [`run()`](../../backend/scripts/bake_reference_index.py) — programmatic entry point; called from `__main__` and from `test_reference_platform_baker.py`.
- [`_chip_paths_for_class()`](../../backend/scripts/bake_reference_index.py) — convention: one subdirectory per source class under `dataset_root`.
- [`_decode_fp16_embedding()`](../../backend/scripts/bake_reference_index.py) — handles the inference response's `fp16_b64` field; raises if dim != 1024.
- Companion: [`backend/scripts/stage_dota_chips.py`](../../backend/scripts/stage_dota_chips.py) — converts DOTA's `labels.json` + flat chip dir into the per-class layout the baker expects. Picks the largest-area annotation per row.
- Read-path companions: [`find_similar_platforms()`](../../backend/reference_platform_db.py#L188-L293) and [`attach_identification_candidates()`](../../backend/reference_platform_db.py#L301-L402) — Plan C consumers of the rows this baker writes; documented in [reference-platform-db.md](reference-platform-db.md).

## Inputs / Outputs
- **Inputs:** seed JSON (one entry per platform with `source_terms_per_dataset`), a dataset root with one subdir per source class, an SPDX license identifier, a max-chips-per-class cap.
- **Outputs:** new/updated rows in `reference_platforms` and `reference_chips`; per-platform centroids; a stdout JSON `{platforms, chips, chips_failed, centroids}`.

## Failure modes
- inference-sam3 returns 503 ("dinov3_sat layer not loaded") → the `/embed` handler's first-line `_ensure_profile("imagery")` call auto-heals on the first request (cold load ~10–30 s); if the failure persists, the operator can `POST /load -d '{"profile":"imagery"}'` manually. See [embed-endpoint.md](../inference/embed-endpoint.md).
- Network timeout → tunable via `REFERENCE_EMBED_TIMEOUT` env (default 60 s).
- Seed file references a dataset key absent from `source_terms_per_dataset` → entry silently skipped (intentional: lets one manifest cover many datasets).
- Chip directory missing for a listed class → log a warning, skip that class.
- Zero chips inserted with seeded platforms → `RuntimeError` (fail-loud).
- `stage_dota_chips.py` called with the wrong `--chips-dir` (e.g. the `chips/` subdir instead of its parent) → the script counts rows-with-annotations and raises `RuntimeError("staged 0 chips from {N} annotated rows (tried {path})")`. Replaces the prior silent `{}` exit-0. The error message names the exact path it tried so operators see the path doubling at the staging step rather than at the downstream bake.

## DOTA proof-of-life recipe
The dataset on disk lives at `./inference-sam3/eval/datasets/dota/` on the host (NOT `dota_val/` — the `_val` directory has a stub schema). It has `labels.json` + a `chips/` subdir. The val set is small (~30 rows / 60 chip files) so not all 18 DOTA-class platforms get chips — typically 10 of the 18 populate after staging. That's expected for proof-of-life.

Cross-container path problem: `inference-sam3` has the DOTA tree at `/app/eval/datasets/dota/` (its bind-mount), but it does NOT mount `/data/datasets/`. `backend` has `/data/datasets/` (the `dataset_data` named volume) but does NOT see the DOTA tree. The bridge is `docker compose cp` from the host into the backend container — the host has the DOTA tree directly under the `./inference-sam3/` bind-mount.

Recipe (run from the repo root on the host):
```
# 1. Copy the DOTA source from the host bind-mount into the backend container.
docker compose cp ./inference-sam3/eval/datasets/dota backend:/data/datasets/dota_src

# 2. Stage chips from the source layout into the per-class layout (inside backend).
# NOTE: --chips-dir is the parent of the `chips/` subdir, NOT the subdir itself.
# labels.json rows carry chip_file="chips/P0003.png" already prefixed; --chips-dir
# is joined with that prefix, so passing .../dota_src/chips would double the path.
docker compose exec -T backend python /app/scripts/stage_dota_chips.py \
    --labels /data/datasets/dota_src/labels.json \
    --chips-dir /data/datasets/dota_src \
    --out-root /data/datasets/reference-chips/dota

# 3. Run the bake (uses INFERENCE_SAM3_URL env or default http://inference-sam3:8001).
docker compose exec -T backend python -m scripts.bake_reference_index \
    --seed /app/scripts/seeds/reference_platforms.seed.json \
    --dataset dota \
    --dataset-root /data/datasets/reference-chips/dota \
    --license CC-BY-4.0 \
    --max-chips-per-class 20

# 4. Optional: drop the temporary copy once the bake is durable.
docker compose exec -T backend rm -rf /data/datasets/dota_src
```

## Cross-references
- [reference-platform-db.md](reference-platform-db.md) — the schema this baker writes into.
- [embed-endpoint.md](../inference/embed-endpoint.md) — the inference route consumed.
- Plan A spec (in-repo): [docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md](../superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md)
- Plan B spec (in-repo): [docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md](../superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md)
