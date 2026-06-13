# Profile Pool Lifecycle

## Purpose

How `inference-sam3` loads, holds, frees model bundles. Profiles cover the operational matrix; switching across them is the **only** safe way to free SAM3's VRAM.

## Profiles

`imagery` is split into per-modality sub-profiles so a tight-VRAM GPU (`SAM3_LOAD_POLICY=dynamic`)
keeps only one modality's models resident. `/detect` auto-routes by request `modality` via
`_profile_for_modality`; the `imagery` union and `all` are resident supersets that satisfy any
subset request without a reload (hot cards). See
[decisions/why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md).

| Profile | Components | Used by | VRAM (FP16) |
|---|---|---|---|
| `imagery_rgb` | `sam3_image`, `dinov3_sat`, `dota_obb`, `grounding_dino` (auto-gated), `mvrsd` (default-on, when `SAM3_LOAD_MVRSD=1`) | RGB imagery ingest | ~5 GB (rgb-only set) |
| `imagery_msi` | `sam3_image`, `dinov3_sat` | Multispectral ingest (SAM3 on RGB preview) | ~5 GB |
| `imagery_sar` | `sam3_image`, `dinov3_sat`, `terramind`, `dota_obb` | SAR ingest | ~7 GB |
| `imagery` | Union of the three above | Hot cards / `/load?profile=imagery` | ~23 GB with every component |
| `fmv` | `sam3_image`, `sam3_video` (multiplex), `dota_obb`, `yoloe` | FMV ingest | ~9 GB measured (16 GiB card) |
| `all` | Union of imagery + fmv | 40+ GiB datacenter GPUs | ~30+ GB |

## Per-GPU replication

`DEVICE=cuda:0,cuda:1` (or `DEVICE=auto` on a multi-GPU host) → each loaded component replicated **once per device**. Request dispatcher round-robins across replicas for parallelism. Single GPU → single replica.

Before building each replica, `_load_profile` calls `_apply_gpu_memory_fraction(device)`: when `SAM3_GPU_MEMORY_FRACTION` is set (a GPU co-tenant like a vLLM server is sharing the cards — auto-detected by `configure_host.py`), it calls `torch.cuda.set_per_process_memory_fraction` so this process can't exceed its share. An over-budget allocation then raises a catchable `OutOfMemoryError` (absorbed by `inference_utils.safe_predict`/`memory_guard`) instead of a context-poisoning `illegal memory access` against the neighbour's VRAM. No-op (0) on a dedicated card. See [decisions/optical-inference-throughput.md](../decisions/optical-inference-throughput.md) (Follow-up part 3).

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
- **Auto-heal swaps from inside a request** (`_ensure_profile`) refuse a real teardown+reload with **503** while any *other* request is in flight (`_active_requests > 1` — the requester has already counted itself), so e.g. an imagery `/detect` can't null `sam3_video` under a running FMV stream. The worker retries 503s. See [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md).
- `POST /unload` — re-exec the container. Returns immediately; clients poll `/health` until the next process is ready.
- `GET /health` — current profile, replica list, active requests, model versions.

## When operators trigger these

Most production deployments preload one profile via `SAM3_PRELOAD_MODELS=1` + `SAM3_PRELOAD_PROFILE=imagery` (or `fmv`). Profile switching reserved for mixed workloads — and even then, the `all` profile on a 40 GiB+ GPU avoids the unload/reload pause entirely.

## Lifespan-level imagery preload

After the explicit `preload_models_on_startup()` step (gated by `SAM3_PRELOAD_MODELS`), the lifespan calls `_ensure_profile(SAM3_RESTING_PROFILE)` so the pool is non-empty by the time the compose healthcheck runs. This keeps the strict healthcheck (`model_loaded AND not model_error`) honest on GPU profiles where `configure_host.py` left `SAM3_PRELOAD_MODELS=0`. `SAM3_RESTING_PROFILE` defaults to the full `imagery` union (hot cards) but `configure_host.py` sets it to **`imagery_rgb`** on dynamic-policy cards — the light per-modality profile that fits a tight GPU at startup while still reporting `model_loaded=true`; the first MSI/SAR/FMV request swaps to that modality's profile. (`SAM3_SKIP_PRELOAD=1` still fully opts out of the lifespan preload.) See [why-preload-imagery-on-startup.md](../decisions/why-preload-imagery-on-startup.md) and [why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md).

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — `_load_profile`, `_unload_pool_locked`, `_next_bundle`, `_acquire_video_bundle`
- [backend-routers/inference-router.md](../backend-routers/inference-router.md) — operator-facing proxy
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md) — the 503 swap guard
