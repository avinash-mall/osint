# `inference-sam3/MODEL_MANIFEST.json` — Weight Registry

**Path:** [inference-sam3/MODEL_MANIFEST.json](../../inference-sam3/MODEL_MANIFEST.json)

## Purpose

Single JSON file listing every model weight the inference service uses, with HuggingFace repo IDs, pinned revisions, file lists, and gating flags. Read at build time by `Dockerfile.gpu` to pre-download weights into the image (offline-deploy compatibility).

## Shape

```json
{
  "sam3_image":     {"repo": "facebook/sam3",      "revision": "...", "gated": true,  "files": [...]},
  "sam3_video":     {"repo": "facebook/sam3.1",    "revision": "...", "gated": true,  "files": [...]},
  "dinov3_sat":     {"repo": "facebook/dinov3-...", "revision": "...", "gated": true,  "files": [...]},
  "prithvi_flood":  {"repo": "ibm-nasa-...",       "revision": "...", "gated": false, "files": [...]},
  "prithvi_burn":   {"repo": "ibm-nasa-...",       "revision": "...", "gated": false, "files": [...]},
  "terramind":      {"repo": "ibm-...",            "revision": "...", "gated": false, "files": [...]},
  "grounding_dino": {"repo": "IDEA-Research/...",  "revision": "...", "gated": false, "files": [...]},
  "yoloe":          {"local": "yoloe-26x-seg.pt"},
  "yoloe_pf":       {"local": "yoloe-26x-seg-pf.pt"},
  "dota_obb":       {"local": "yolo11n-obb.pt"}
}
```

## Why this design

- **Pinned revisions.** Reproducibility for air-gap deployments: the same build, run twice, produces identical model behavior because revisions are SHAs not tags.
- **Gating column** so the build can skip gated weights when `HF_TOKEN` is absent or set `SAM3_WEIGHTS_SOURCE=mirror` to use the `1038lab/sam3` mirror instead.
- **Local-file entries** for weights that ship bundled in the image (YOLOE, DOTA-OBB) and aren't fetched from the Hub.

## Cross-references

- [inference-sam3/Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu) — consumes this at build time
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
- [conventions/adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)
