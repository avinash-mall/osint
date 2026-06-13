# `inference-sam3/MODEL_MANIFEST.json` — Weight Registry

**Path:** [inference-sam3/MODEL_MANIFEST.json](../../inference-sam3/MODEL_MANIFEST.json)

## Purpose

Single JSON file listing every model weight the `inference-sam3` image bakes:
HuggingFace repo IDs, pinned revisions, file lists, gating flags, and local
asset paths. The LAE-DINO model is owned by the separate `inference-lae`
sidecar and is intentionally absent from this manifest.

## Shape

```json
{
  "sam3_image":     {"repo": "facebook/sam3",      "revision": "...", "gated": true,  "files": [...]},
  "sam3_video":     {"repo": "facebook/sam3.1",    "revision": "...", "gated": true,  "files": [...]},
  "dinov3_sat":     {"repo": "facebook/dinov3-...", "revision": "...", "gated": true,  "files": [...]},
  "terramind":      {"repo": "ibm-...",            "revision": "...", "gated": false, "files": [...]},
  "yoloe":          {"local": "yoloe-26x-seg.pt"},
  "yoloe_pf":       {"local": "yoloe-26x-seg-pf.pt"},
  "dota_obb":       {"local": "yolo26m-obb.pt"},
  "mvrsd/mvrsd_yolo11m": {"url": "${MVRSD_WEIGHTS_URL}", "gated": true, "files": [{"path": "/models/mvrsd/mvrsd_yolo11m.pt"}]}
}
```

## Why this design

- **Pinned revisions** — reproducibility for air-gap deploys: same build run twice → identical model behavior because revisions are SHAs not tags.
- **Gating column** — build can skip gated weights when `HF_TOKEN` absent, or set `SAM3_WEIGHTS_SOURCE=mirror` to use the `1038lab/sam3` mirror.
- **Local-file entries** — weights bundled in the image (YOLOE, DOTA-OBB), not fetched from the Hub.
- **`url` + build-ARG entries** — the MVRSD military-vehicle specialist weight is a GitHub release asset baked from `${MVRSD_WEIGHTS_URL}` at build time (empty default = skip-if-empty no-op; the runner honour-gates). See [mvrsd-specialist.md](mvrsd-specialist.md).
- The FAIR1M-OBB detector and RemoteCLIP verifier were removed (2026-05-31)
  — see [decisions/removed-fair1m-and-remoteclip.md](../decisions/removed-fair1m-and-remoteclip.md).
- Generic IDEA-Research Grounding-DINO was replaced by the LAE-DINO sidecar;
  the `grounding_dino` runtime key now names the client layer, not an in-process
  baked checkpoint.

## Cross-references

- [inference-sam3/Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu) — consumes this at build time
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
- [conventions/adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)
