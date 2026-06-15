# Removed the grounding_dino layer + LAE-DINO sidecar

**Status:** shipped (2026-06-14). Deletes the `grounding_dino` inference layer
(`inference-sam3/grounding_dino.py`, `grounding_dino_gate.py`), the `inference-lae`
sidecar service + `inference-lae/` image, and every env/manifest/registry/test/doc
reference (and supersedes the now-deleted `why-lae-dino-replaces-grounding-dino`
decision) — the open-vocab box detector is gone entirely.

## Why

The `grounding_dino` layer (backed by the LAE-DINO remote-sensing detector in the
`inference-lae` sidecar) was **not improving detection quality on this deployment**,
and could not even run:

1. **Cannot run on the target GPU.** The host is an RTX 5070 Ti — **compute
   capability 12.0 (sm_120, Blackwell)**. `inference-lae` is pinned to
   **torch 2.1 / cu121** (the newest stack with prebuilt `mmcv` wheels), whose
   CUDA kernels top out at **sm_90 (Hopper)**. On sm_120 it fails with "no kernel
   image for execution on the device." mmcv has **no release** supporting a
   Blackwell-capable PyTorch (≥2.7 / cu128+), so a rebuild isn't available either.
2. **Contributed zero detections in the default config.** The layer was
   default-OFF (`SAM3_LOAD_GROUNDING_DINO`), profile-gated (`--profile lae`, never
   started), and additionally auto-gated by `grounding_dino_gate` whenever the
   prompts were already in the common SAM3+DOTA-OBB vocabulary. Production
   `candidates_by_layer` showed `grounding_dino: 0`.
3. **Generic open-vocab detectors transfer poorly to aerial anyway.** A measured
   A/B (DOTA val) plus the literature put zero-shot generic OVD far below SAM3 on
   overhead imagery; LAE-DINO (the RS-finetuned variant) would help *if it ran*,
   but it can't here, so it was dead weight: an unused HTTP client, a heavy
   profile-gated mmcv image, and config/manifest/registry/test surface.

Keeping a layer that can't run and never fires is pure maintenance cost (and a
trap — operators could set `SAM3_LOAD_GROUNDING_DINO=1` and get a silent no-op or
a crash). Removing it simplifies the inference path to **SAM3 + DOTA-OBB + MVRSD +
DINOv3-SAT embeddings**.

## What was removed

- **inference-sam3:** `grounding_dino.py`, `grounding_dino_gate.py` (deleted); the
  layer block in `_detect_pipeline`, the `force_grounding_dino` meta, the
  `grounding_dino_gated` response field, the load flag / profile component / health
  slug / bundle key in `main.py`; the WBF trust weight in `fusion.py`; the
  manifest note.
- **Sidecar + config:** the `inference-lae` Compose service + `lae` profile +
  `LAE_DINO_URL` / `GROUNDING_DINO_*` env, the `inference-lae/` image directory,
  and the `.env` / `.env.example` entries. `configure_host.py` now assigns all
  free GPUs to inference-sam3 (no LAE card carve-out).
- **Backend/scripts/tests:** the layer registry row, `OPEN_VOCAB_SOURCES` entry,
  `force_grounding_dino` plumbing in the benchmark/calibration scripts, and the
  grounding_dino tests.
- **Docs:** the three `inference/grounding-dino*` / `lae-dino-sidecar` module docs
  and the three `why-*` decision docs, plus cross-references.

## Consequences

- If RS open-vocab detection is wanted later, adopt a **Blackwell-compatible**
  path (e.g. a YOLO-World/YOLOE fine-tuned on RS data, which runs natively on
  cu13x) rather than resurrecting the mmcv/cu121 sidecar. See the perf work in
  [sam3-compile-and-chip-padding-2026-06-14.md](sam3-compile-and-chip-padding-2026-06-14.md)
  for why the SAM3-side speedups made the open-vocab role viable to keep on SAM3.
- WBF/NMS fusion, evidence ranking, and the layer registry are unchanged except
  for the dropped layer.

## Cross-references

- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — modality dispatch (now SAM3 + DOTA-OBB + MVRSD)
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md) — WBF weights
- [decisions/removed-fair1m-and-remoteclip.md](removed-fair1m-and-remoteclip.md) — prior detector removal
