# Profile Pool Lifecycle

## Purpose

How `inference-sam3` loads, holds, frees model bundles. Three profiles cover the operational matrix; switching across them is the **only** safe way to free SAM3's VRAM.

## Profiles

| Profile | Components | Used by | VRAM (FP16) |
|---|---|---|---|
| `imagery` | `sam3_image`, `dinov3_sat`, `prithvi`, `terramind`, `dota_obb`, `grounding_dino`, optional `remoteclip`, `yoloe` | Imagery ingest (incl. `model=yolo26` path) | ~24 GB with all components before verifier |
| `fmv` | `sam3_image`, `sam3_video` (multiplex), `dota_obb`, `yoloe` | FMV ingest | ~12 GB |
| `all` | Union of both | 40+ GiB datacenter GPUs | ~30+ GB |

## Per-GPU replication

`DEVICE=cuda:0,cuda:1` (or `DEVICE=auto` on a multi-GPU host) → each loaded component replicated **once per device**. Request dispatcher round-robins across replicas for parallelism. Single GPU → single replica.

## State machine

```
┌──────────────┐    /load?profile=imagery     ┌──────────┐
│ initial      │ ─────────────────────────► │ imagery   │
│ (empty pool) │                              │ loaded    │
└──────┬───────┘                              └─────┬─────┘
       │                                            │
       │ /load?profile=fmv (from initial)           │ /load?profile=fmv (from imagery)
       │   → load fmv components                    │   → FAILS: cannot free SAM3 cleanly
       │                                            │
       ▼                                            ▼
   ┌──────────┐                                ┌──────────┐
   │ fmv      │                                │ MUST     │
   │ loaded   │                                │ /unload  │
   └─────┬────┘                                │ FIRST    │
         │                                     └─────┬────┘
         │                                           │
         │ /unload → process re-execs                ▼
         │                                     /unload (re-exec)
         └─────────────────────────────────────► initial
```

**Key constraint:** `/load` from `imagery` to `fmv` (or vice versa) **doesn't work** — SAM3's CUDA memory cannot free without process restart. Only reliable transition: `/unload` → cold start → `/load`.

## Endpoints

- `POST /load?profile=imagery|fmv|all` — load if pool empty; 409 if a different profile already loaded.
- `POST /unload` — re-exec the container. Returns immediately; clients poll `/health` until the next process is ready.
- `GET /health` — current profile, replica list, active requests, model versions.

## When operators trigger these

Most production deployments preload one profile via `SAM3_PRELOAD_MODELS=1` + `SAM3_PRELOAD_PROFILE=imagery` (or `fmv`). Profile switching reserved for mixed workloads — and even then, the `all` profile on a 40 GiB+ GPU avoids the unload/reload pause entirely.

## Lifespan-level imagery preload

After the explicit `preload_models_on_startup()` step (gated by `SAM3_PRELOAD_MODELS`), the lifespan unconditionally calls `_ensure_profile("imagery")` so the pool is non-empty by the time the compose healthcheck runs. This keeps the strict healthcheck (`model_loaded AND not model_error`) honest on GPU profiles where `configure_host.py` left `SAM3_PRELOAD_MODELS=0`. Opt out with `SAM3_SKIP_PRELOAD=1` on memory-constrained GPUs that need to load on first request. See [why-preload-imagery-on-startup.md](../decisions/why-preload-imagery-on-startup.md).

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — `_load_profile`, `_unload_pool_locked`, `_next_bundle`, `_acquire_video_bundle`
- [backend-routers/inference-router.md](../backend-routers/inference-router.md) — operator-facing proxy
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md)
