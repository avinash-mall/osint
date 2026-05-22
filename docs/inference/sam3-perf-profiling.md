# `inference-sam3/sam3_perf.py` — Stage Timing + SDPA Backend

**Path:** [inference-sam3/sam3_perf.py](../../inference-sam3/sam3_perf.py)
**Lines:** ~72
**Depends on:** `torch`

## Purpose

CUDA-synchronous per-stage timing helper breaking down `/detect` latency into named stages (encode, sam3, dota, gdino, dinov3, fusion, etc.). Also chooses the most efficient `torch.nn.functional.scaled_dot_product_attention` backend per GPU generation.

## Key symbols

- [`stage_timer`](../../inference-sam3/sam3_perf.py#L14) — context manager: `with stage_timer(timings, "sam3"): ...` accumulates elapsed ms into `timings["sam3"]`.
- [`is_blackwell_consumer`](../../inference-sam3/sam3_perf.py#L39) — true on RTX 50-series (sm_120).
- [`pin_sdpa_backend`](../../inference-sam3/sam3_perf.py#L55) — selects flash-attn vs cuDNN SDPA backend; flash preferred on Hopper+, cuDNN on Ampere consumer.

## Why this design

- **Sync timing** with `torch.cuda.synchronize()` — async kernel launches won't make a stage look faster than it is. Costs a few percent of real run time but essential for profiling.
- **Backend selection at startup** — production code doesn't pay per-call overhead choosing the SDPA backend.

## Where it shows up

`/health` surfaces per-stage P50/P95 latencies from these timings — under `metrics.<slug>.p50_ms` and `metrics.<slug>.p95_ms`.

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — `_record_metric` + `_metrics_snapshot` consume these
- [benchmarks/sam3-perf-phases.md](../benchmarks/sam3-perf-phases.md)
- Tests: [inference-sam3/tests/test_sam3_perf.py](../../inference-sam3/tests/test_sam3_perf.py)
