# Inference Unit Tests

**Path:** [inference-sam3/tests/](../../inference-sam3/tests/)
**Runner:** `cd inference-sam3 && python -m pytest tests/ -q`

## Tests

| File | Covers |
|---|---|
| [test_fusion.py](../../inference-sam3/tests/test_fusion.py) | Mask-aware NMS, OBB extraction, COCO RLE round-trip, cross-layer provenance |
| [test_box_prompt_native.py](../../inference-sam3/tests/test_box_prompt_native.py) | Box-prompted SAM3 native path (replaced wrapper code) |
| [test_oom_recovery.py](../../inference-sam3/tests/test_oom_recovery.py) | `inference_utils.safe_predict` graceful OOM handling |
| [test_prompts_loader.py](../../inference-sam3/tests/test_prompts_loader.py) | `_fetch_default_prompts` + caching + 503 fallback |
| [test_sam3_perf.py](../../inference-sam3/tests/test_sam3_perf.py) | `stage_timer` accumulation, backend selection |
| [test_main_stubbed.py](../../inference-sam3/tests/test_main_stubbed.py) | `/detect` request validation, image YOLOE-layer rejection, precision prompt defaults, source-layer tags, specialist gates, semantic-verifier metadata absence after RemoteCLIP removal |
| [test_precision_benchmark.py](../../inference-sam3/tests/test_precision_benchmark.py) | Live-service precision benchmark; skipped when `INFERENCE_URL` unreachable |
| [test_grounding_dino_gate.py](../../inference-sam3/tests/test_grounding_dino_gate.py) | `is_common` + `should_run_grounding_dino` |
| [test_inference_utils.py](../../inference-sam3/tests/test_inference_utils.py) | YOLO optimization helpers, memory guard |
| [test_chip_prep_perf.py](../../inference-sam3/tests/test_chip_prep_perf.py) | `backend/chip_prep_profiler.py` no-op-when-disabled, stage accumulation, CSV side-channel |

## conftest

[conftest.py](../../inference-sam3/tests/conftest.py) puts `inference-sam3/` on `sys.path`, pre-stubs `psutil` + `torch` in `sys.modules` when absent → the suite is collectable from the repo root (not only `cd inference-sam3`), removing the implicit ordering dependency where `test_main_stubbed.py` had to run first to seed `sys.modules` for `import main`.

## Stubbed-model strategy

The full inference image requires CUDA. `test_main_stubbed.py` seeds the in-memory model pool with stubs and injects lightweight module stubs where developer environments lack GPU-only packages → request validation, prompt resolution, specialist gating, fusion testable CPU-only.

## Cross-references

- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md)
- [inference/grounding-dino-gate.md](../inference/grounding-dino-gate.md)
- [inference/sam3-perf-profiling.md](../inference/sam3-perf-profiling.md)
