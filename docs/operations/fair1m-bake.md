# FAIR1M-2.0 OBB bake — train and ship a fine-grained aerial checkpoint

## Purpose

Ship a trained checkpoint for the FAIR1M-2.0 fine-grained OBB specialist so
the [inference-sam3/fair1m_obb.py](../../inference-sam3/fair1m_obb.py)
runner becomes active. The runner gracefully no-ops when no weights are
present (returns `{model: None}`), so the absence is invisible at runtime
on fresh installs. This runbook documents the workflow for operators who
need the FAIR1M-2.0 fine-grained labels (Boeing 737/747/777/787, Warship,
Dump Truck, Tractor, ...).

## Why operator-baked, not shipped

- FAIR1M-2.0 dataset access requires GaoFen Challenge registration (free,
  but not auto-downloadable). Project policy: no runtime downloads.
- Training on a 24 GB GPU takes ~12 hours; not feasible in CI.
- The resulting checkpoint is ~80 MB and gets `.gitignore`d under
  `assets/static/inference-weights/fair1m/`. Same workflow as the
  per-detector calibration files in
  [calibration-shipping.md](calibration-shipping.md).

## Steps

### 1. Download the FAIR1M-2.0 dataset

Register at https://www.gaofen-challenge.com/benchmark and download
the FAIR1M-2.0 train + validation splits. The dataset ships as a
labelled tile collection with XML annotations and 37 sub-class labels.

### 2. Convert to Ultralytics OBB format

The Ultralytics `yolo obb train` task expects one `.txt` per image with
8-corner polygons plus a class index per line:

```
<class_idx> x1 y1 x2 y2 x3 y3 x4 y4
```

Use `scripts/prepare_fair1m_dataset.py` (TODO: full conversion script
not in scope for the plumbing commit; the outline below documents what
the converter must do):

```python
# scripts/prepare_fair1m_dataset.py — TODO
# 1. Walk FAIR1M XML files; parse the 37 class names + polygon corners.
# 2. Map FAIR1M class name → index using inference-sam3/fair1m_obb.FAIR1M_CLASSES
#    (the array order is the canonical FAIR1M index order; keep in sync).
# 3. Emit one .txt per image with normalised polygon corners (Ultralytics
#    convention: corners in image-pixel coordinates / image dims).
# 4. Write a fair1m.yaml manifest listing train/val image directories and the
#    37 names in the same order as FAIR1M_CLASSES.
```

### 3. Train on a 24 GB GPU

```bash
yolo obb train \
    data=fair1m.yaml \
    model=yolo11m-obb.pt \
    epochs=100 \
    imgsz=1024 \
    batch=8 \
    device=0 \
    project=runs/fair1m-obb \
    name=v1
```

Expected wall-clock: ~12 hours on a 24 GB card. The output is at
`runs/fair1m-obb/v1/weights/best.pt`.

### 4. Place the checkpoint

```bash
mkdir -p assets/static/inference-weights/fair1m/
cp runs/fair1m-obb/v1/weights/best.pt \
   assets/static/inference-weights/fair1m/yolo11m-obb-fair1m.pt
```

The `.pt` file is `.gitignore`d (~80 MB) — do not commit it. Update the
sha256 anchor in [../../inference-sam3/MODEL_MANIFEST.json](../../inference-sam3/MODEL_MANIFEST.json):

```bash
sha256sum assets/static/inference-weights/fair1m/yolo11m-obb-fair1m.pt
# paste the digest into the manifest's fair1m/yolo11m-obb-fair1m entry
```

### 5. Bake into the assets image and rsync

The existing assets-image bake workflow (see
[calibration-shipping.md](calibration-shipping.md) for the COPY +
entrypoint rsync pattern) is the canonical path. When an operator first
needs FAIR1M weights, the bake hook in
[../../assets/Dockerfile](../../assets/Dockerfile) and the rsync in
[../../assets/scripts/entrypoint.sh](../../assets/scripts/entrypoint.sh)
need a one-line extension to mirror `inference-weights/fair1m/` onto the
shared `inference_weights` volume — that wiring is intentionally not in
the plumbing commit and lands the first time real weights are available.

### 6. Restart inference

```bash
docker compose restart inference
curl -s http://localhost:8001/health \
  | jq '.model_versions.fair1m_obb'
```

Expected: `loaded: true`, `class_count: 37`, `error: null`. The
specialist now fires automatically on prompts that mention FAIR1M
sub-class vocabulary (operator override via `metadata.force_fair1m_obb=true`).

## Verification

After the bake, re-run the precision benchmark with the FAIR1M-relevant
slice:

```bash
python scripts/compare_inference_layers.py \
    --url http://172.18.0.2:8001 \
    --slice dota --max-chips 30 --repeats 3 \
    --output bench/fair1m_check.md \
    --layers "sam3,dota_obb,fair1m_obb"
```

Per the approved scope plan, aircraft AP is expected to rise from ~0.36
(single "plane" bucket) to >0.6 once FAIR1M fires on airframe-family
prompts.

## Cross-references

- [../inference/fair1m-obb-specialist.md](../inference/fair1m-obb-specialist.md) — runner module doc
- [../decisions/why-fair1m-specialist.md](../decisions/why-fair1m-specialist.md) — decision rationale
- [calibration-shipping.md](calibration-shipping.md) — precedent for the bake-and-rsync pattern
- [../conventions/adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)
