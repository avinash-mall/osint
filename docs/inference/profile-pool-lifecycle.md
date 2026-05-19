# Profile Pool Lifecycle

## Purpose

Explain how `inference-sam3` loads, holds, and frees model bundles. Three profiles cover the operational matrix; switching across them is the **only** safe way to free SAM3's VRAM.

## Profiles

| Profile | Components | Used by | VRAM (FP16) |
|---|---|---|---|
| `imagery` | `sam3_image`, `dinov3_sat`, `prithvi`, `terramind`, `dota_obb`, `grounding_dino` | Imagery ingest | ~22 GB with all components |
| `fmv` | `sam3_image`, `sam3_video` (multiplex), `dota_obb`, `yoloe` | FMV ingest | ~12 GB |
| `all` | Union of both | 40+ GiB datacenter GPUs | ~30+ GB |

## Per-GPU replication

When `DEVICE=cuda:0,cuda:1` (or `DEVICE=auto` on a multi-GPU host), each loaded component is replicated **once per device**. The request dispatcher round-robins across replicas for parallelism. Single GPU → single replica.

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

**Key constraint:** `/load` from `imagery` to `fmv` (or vice versa) **doesn't work** — SAM3's CUDA memory cannot be freed without process restart. The only reliable transition is `/unload` → cold start → `/load`.

## Endpoints

- `POST /load?profile=imagery|fmv|all` — load if pool is empty; reject with 409 if a different profile is already loaded.
- `POST /unload` — re-exec the container. Returns immediately; clients poll `/health` until the next process is ready.
- `GET /health` — current profile, replica list, active requests, model versions.

## When operators trigger these

Most production deployments preload one profile via `SAM3_PRELOAD_MODELS=1` + `SAM3_PRELOAD_PROFILE=imagery` (or `fmv`). Profile switching is reserved for mixed workloads — and even then, the `all` profile on a 40 GiB+ GPU avoids the unload/reload pause entirely.

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — `_load_profile`, `_unload_pool_locked`, `_next_bundle`, `_acquire_video_bundle`
- [backend-routers/inference-router.md](../backend-routers/inference-router.md) — operator-facing proxy
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md)
