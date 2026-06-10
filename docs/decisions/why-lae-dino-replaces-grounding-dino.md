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
upstream pins (torch 1.10+cu113, transformers 4.42) are irreconcilable with the
main service, which needs torch 2.x + transformers ≥4.56 for SAM 3, TerraMind,
Prithvi and YOLO26. You cannot host both in one Python process. A separate
container with its own dependency closure is the only clean boundary. As a
bonus, it frees VRAM in the inference-sam3 process (the client does no GPU work).

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

## Known risks / validation owed (cannot be verified offline)

1. **mmcv build against modern torch.** The sidecar Dockerfile compiles
   `mmcv>=2.0,<2.2` from source against the host's torch/CUDA (not LAE-DINO's
   2021 pins, which can't target H100/Blackwell). This is the #1 thing to
   confirm on a real GPU host.
2. **Fork API on torch 2.x.** The `mmdet==3.3.0` fork is exercised on a newer
   torch than upstream tested; smoke-test `/detect` before trusting it.
3. **Box convention.** `pred_instances.bboxes` is assumed xyxy (mmdet DINO
   standard) — verify once running.
4. **BERT offline.** `google-bert/bert-base-uncased` is baked and the config's
   `language_model.name` is overridden to the local dir; confirm no runtime HF
   fetch with `HF_HUB_OFFLINE=1`.

## Cross-references

- [inference/lae-dino-sidecar.md](../inference/lae-dino-sidecar.md)
- [inference/grounding-dino-detector.md](../inference/grounding-dino-detector.md)
- [inference/grounding-dino-gate.md](../inference/grounding-dino-gate.md)
- [decisions/why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md)
- [decisions/why-branch-scoped-default.md](why-branch-scoped-default.md)
