# Why LAE-DINO replaces IDEA-Research Grounding-DINO

**Status:** accepted
**Date:** 2026-06-10
**Scope:** `inference-sam3/grounding_dino.py`, new `inference-lae/` service, `docker-compose.yml`

## Decision

Replace the natural-image **IDEA-Research Grounding-DINO** behind the
`grounding_dino` detector layer with **LAE-DINO** ("Locate Anything on Earth",
AAAI'25) — a Grounding-DINO derivative fine-tuned on the LAE-1M aerial/satellite
corpus (DIOR + DOTAv2 + FAIR1M + xView + NWPU-VHR-10 + RSOD + HRSC2016 …).
LAE-DINO runs in a **separate `inference-lae` sidecar container**; the existing
`grounding_dino` module is now a thin HTTP client.

## Why this design

**Use-case fit.** The old weights are trained on COCO-style ground-level imagery
and produce sub-optimal, false-positive-heavy results on overhead scenes (the
in-code note recorded a ~69% FP rate). LAE-DINO is purpose-built for remote
sensing — 87.3 AP50 on DIOR, 51.5 mAP on DOTAv2 — and is **MIT-licensed** with
non-gated weights on HuggingFace (`ML4Sustain/LAE-DINO`), which satisfies the
offline/open hard rules.

**Why a sidecar, not in-process.** LAE-DINO is a *forked mmdetection*: it
registers a custom `LAEDINO(DINO)` model plus the paper's DVC (Dynamic
Vocabulary Construction) and VisGT modules, and is driven by mmengine/mmcv. Its
transformers pin (4.42.3) is irreconcilable with the main service, which needs
transformers >=4.56 for SAM 3, TerraMind, DINOv3-SAT, and YOLO26. You cannot host
both in one Python process. A separate container with its own dependency closure
is the only clean boundary. As a bonus, it frees VRAM in the inference-sam3
process (the client does no GPU work).

**Build stack.** The sidecar pins **torch 2.1.0 + cu121** with a *prebuilt* mmcv
2.1.0 wheel (the newest combo mmcv ships wheels for and the mmdet-3.3 fork is
validated against) — NOT the host's bleeding-edge cu130 GPU profile (mmcv has no
cu130 wheels) and NOT LAE-DINO's 2021 upstream pins (torch 1.10+cu113, which
can't target modern GPUs). cu121 runs natively on A100/H100 via the host's newer
driver. The fork also needs `transformers==4.42.3` pinned (its
requirements/multimodal.txt leaves it unpinned → resolves to a 5.x that crashes
mmdet import) plus `clip-anytorch` + `open_clip_torch` (imported at model load
but never instantiated — the LAE-1M model grounds via BERT).

**Why keep the layer name `grounding_dino`.** The routing key threads through the
auto-gate, the profile pool, `source_layer` provenance, the `/health` payload,
the `grounding_dino_gated` response field, per-class calibration
(`backend/calibration-temperature.md`) and the frontend layer toggle. LAE-DINO
*is* a Grounding-DINO derivative filling the identical role (open-vocab
text-to-box specialist, gated on uncommon prompts), so renaming the key would
ripple across backend + frontend for zero functional gain. A full rename to
`lae_dino` is a deliberate **follow-up**, out of scope here to keep the diff
reviewable. `/health` reports the true model id (`LAE-DINO (lae_dino_swint_lae1m)`)
so provenance stays honest.

## Alternatives rejected

- **Esri/ArcGIS GroundingDINO** ([item e60d9745…](https://www.arcgis.com/home/item.html?id=e60d974556fa45db95f5bf73caf2421a),
  [docs](https://doc.arcgis.com/en/pretrained-models/latest/imagery/introduction-to-groundingdino.htm)):
  it is the *generic* IDEA-Research model repackaged as a proprietary `.dlpk`,
  credential-gated, with no supported path to plain PyTorch/HF — fails the
  offline/open rules and isn't even RS-specific. Rejected.
- **MM-Grounding-DINO** (`openmmlab-community/*`, transformers-native): a clean
  drop-in but still natural-image trained — does not meet the "use-case-specific"
  requirement. Rejected for this change (kept as the easy fallback if LAE-DINO's
  sidecar build proves impractical).
- **In-process mmcv rebuild on torch 2.x:** would force a transformers downgrade
  that breaks SAM 3. Rejected.

## Validation status — BUILT & TESTED on 4× A100 80GB (2026-06-10)

The sidecar was built and exercised on the GPU host. Results:

- **Build:** clean on torch 2.1.0+cu121 / mmcv 2.1.0 (prebuilt wheel) / mmengine
  0.10.4 / the mmdet-3.3 fork (editable, `--no-build-isolation`). Image 17.2 GB.
- **Model load:** LAE-DINO + BERT load in ~15 s, fully offline
  (`HF_HUB_OFFLINE=1`); no runtime HF fetch.
- **Direct `/detect`:** 54 detections on a real 1024² chip; **median 187 ms/call
  (~5.4 req/s)** on one A100.
- **Cross-service:** inference-sam3 `/detect` with `force_grounding_dino` →
  `candidates_by_layer.grounding_dino = 75` via the HTTP client, fused into final
  detections. Common-vocab prompt without force → correctly auto-gated.
- **Box convention** confirmed xyxy. **`pred_instances.label_names`** carries the
  matched entity strings (mapped back to canonical prompts by the client).

### Build quirks that had to be solved (encoded in the Dockerfile / app.py)

1. **`--no-build-isolation`** for the fork's editable install (its setup.py
   imports torch at build time).
2. **`huggingface-cli` → `hf`** for the weight bake (the CLI was removed in the
   newer huggingface_hub).
3. **`transformers==4.42.3`** pin (unpinned → 5.x → `NameError: nn` on mmdet
   import).
4. **`clip-anytorch` + `open_clip_torch`** (imported by the fork's
   clip_text_backbone at module load).
5. **`DetInferencer(palette="random")`** not `"none"` — `"none"` makes it build
   the test dataset (missing training-annotation JSONs).
6. **Hoist `cfg.test_pipeline`** onto the wrapped ConcatDataset cfg so
   `_init_pipeline` finds it.
7. **`chunked_size` disabled** — the fork's chunked predict path is broken
   (`LAEDINOHead.predict() ... ** must be a mapping, not tuple`); the client
   chunks prompts instead.

### Residual notes

- GSD: LAE-DINO is trained on sub-meter aerial; on 10 m Sentinel-2 it under-
  detects. Feed it sub-meter chips.
- The GD score floor on this host is 0.20 (legacy `.env`); 0.30 is the
  recommended LAE-DINO default (`.env.example`).

## Cross-references

- [inference/lae-dino-sidecar.md](../inference/lae-dino-sidecar.md)
- [inference/grounding-dino-detector.md](../inference/grounding-dino-detector.md)
- [inference/grounding-dino-gate.md](../inference/grounding-dino-gate.md)
- [decisions/why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md)
- [decisions/why-branch-scoped-default.md](why-branch-scoped-default.md)
