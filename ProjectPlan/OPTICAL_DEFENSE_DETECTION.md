# Optical-Defense Detection Runbook

Date: 2026-04-30

This document describes the implemented optical-only object detection workflow. It is intentionally scoped to available RGB/optical imagery. SAR, radar, thermal, RF, hyperspectral, and optical-SAR fusion are not part of this build.

## Current State

The copied training run at `training_dataset/runs/geoint_yolov8` was audited with:

```bash
python inference/audit_training_run.py --run-dir training_dataset/runs/geoint_yolov8
```

Key result:

| Metric | Value |
|---|---:|
| Final precision | 0.48009 |
| Final recall | 0.52526 |
| Final mAP50 | 0.49268 |
| Final mAP50-95 | 0.39083 |
| Best recall | 0.52694 at epoch 99 |
| Best mAP50-95 | 0.39208 at epoch 96 |

The run confirms the earlier failure pattern: weak recall, many false positives, long-tail class imbalance, over-weighted easy infrastructure classes, and HRSC numeric class leakage risk.

## Implemented Changes

### Taxonomy And Threshold Policy

Shared policy modules now normalize detections into mission-relevant parent classes:

- `aircraft`
- `ship`
- `vehicle`
- `military_vehicle`
- `storage_tank`
- `bridge`
- `harbor`
- `airfield`
- `building`
- `infrastructure`

Distractors are disabled by default:

- `dam`
- `recreation`
- `water`
- `unknown`

Threshold profiles:

- `recall_review`: low confidence floor, exposes review candidates.
- `balanced`: stricter default analyst workflow.
- `high_precision`: suppresses weak detections for high-confidence views.

Default compose profile:

```yaml
DETECTION_THRESHOLD_PROFILE=recall_review
CONFIDENCE_THRESHOLD=0.12
NMS_IOU_THRESHOLD=0.55
MODEL_VERSION=geoint-yolov8-obb-optical-defense
```

### Inference Output

The inference service still returns OBB detections, but every detection is now enriched with:

- `class`: parent class used by the application
- `original_class`: original model/source class
- `parent_class`
- `calibrated_confidence`
- `class_threshold`
- `review_status`
- `threshold_profile`
- `model_version`
- `taxonomy_version`

`review_status` is one of:

- `review_candidate`
- `high_confidence`
- `below_class_threshold`
- `disabled_distractor`

Only enabled classes above the active class threshold are emitted.

### Worker Tiling And Dedupe

The worker now defaults to:

```text
INFERENCE_CHIP_SIZE=1024
INFERENCE_CHIP_OVERLAP=256
MAX_INFERENCE_CHIPS=0
```

`MAX_INFERENCE_CHIPS=0` means full raster coverage. If a cap is configured later, every stored detection and ingest progress payload records `coverage_fraction`, `planned_chips`, `source_total_chips`, and `sampling_enabled`.

Cross-chip dedupe now prefers OBB polygon IoU and falls back to HBB IoU. Dedupe buckets use `parent_class`, so equivalent source labels merge correctly.

### Dataset Preparation

`inference/prepare_datasets.py` now defaults to the optical-defense taxonomy:

```bash
python inference/prepare_datasets.py \
  --datasets xview dota fair1m dior sodaa hrsc2016 \
  --tile-size 1024 \
  --overlap 0.2 \
  --include-empty-ratio 0.05 \
  --hard-negative-ratio 0.5 \
  --max-instances-per-class 50000 \
  --clean
```

Outputs:

- `training_dataset/yolo/data.yaml`
- `training_dataset/yolo/classes.json`
- `training_dataset/yolo/taxonomy.json`
- `training_dataset/yolo/manifest.jsonl`
- `training_dataset/yolo/split_summary.json`
- `training_dataset/yolo/class_distribution.csv`
- `training_dataset/yolo/source_distribution.csv`
- `training_dataset/yolo/object_size_distribution.csv`
- `training_dataset/yolo/summary.json`
- `training_dataset/yolo/summary.csv`

Dams, sports courts, water-only labels, and unknown labels become hard-negative tiles unless `--include-distractors` is set.

HRSC annotations without readable `sysdata.xml` class names now fall back to `hrsc_ship`, not numeric IDs.

### Training Promotion Gate

Training now defaults to a stronger OBB baseline:

```bash
python inference/train_model.py \
  --data training_dataset/yolo/data.yaml \
  --base-model yolov8s-obb.pt \
  --epochs 100 \
  --imgsz 1024 \
  --batch auto \
  --device auto
```

Promotion is blocked unless final validation recall is at least `0.525`:

```bash
--min-recall 0.525
```

Use `--promote-anyway` only after reviewing per-class metrics and failure scenes.

### Backend And UI

Detection API and GeoJSON now expose:

- `parent_class`
- `original_class`
- `calibrated_confidence`
- `review_status`
- `threshold_profile`
- `class_threshold`
- `model_version`
- `taxonomy_version`
- `chip_id`
- `coverage_fraction`

The map UI displays parent class, original class, review status, profile, and coverage in detection popups and the detail panel.

## Required Next Steps

### 1. Recover Or Regenerate Dataset Artifacts

The copied `runs` folder is diagnostic only. It does not contain the actual YOLO dataset used for training.

Required before retraining:

```text
training_dataset/yolo/data.yaml
training_dataset/yolo/classes.json
training_dataset/yolo/manifest.jsonl
training_dataset/yolo/taxonomy.json
training_dataset/yolo/split_summary.json
training_dataset/yolo/class_distribution.csv
training_dataset/yolo/source_distribution.csv
training_dataset/yolo/object_size_distribution.csv
```

Best path:

1. Copy `training_dataset/yolo/` from the training server if it still exists.
2. If not available, copy or stage the raw datasets under `training_dataset/raw/`.
3. Regenerate with `inference/prepare_datasets.py`.

If the copied `training_dataset/yolo/` contains `data.yaml`, `classes.json`, and `manifest.jsonl` but is missing `taxonomy.json`, repair the metadata in place:

```bash
python inference/repair_yolo_artifacts.py --yolo-root training_dataset/yolo
```

This writes `taxonomy.json`, `class_mapping.csv`, `split_summary.json`, and `source_distribution.csv`, and rewrites `data.yaml` to use the local dataset path. If the copied folder does not include `train/`, `val/`, and `test` label directories, exact class distribution reports cannot be reconstructed.

### 2. Build A Fixed Failure Benchmark

Create a small validation folder with hand-checked scenes covering:

- Adjacent vehicles where only one vehicle was detected.
- Dam/infrastructure false positives.
- Dense ports and harbors.
- Airfields and parked aircraft.
- Storage tank farms.
- Roads with many small vehicles.
- Ships near shore.
- Empty hard-negative scenes.

For every scene, preserve:

- source image
- expected object list
- class labels
- polygons or OBBs if available
- reason the scene is included

### 3. Run Dataset Audit Before Training

After dataset preparation, inspect:

```bash
type training_dataset/yolo/summary.json
type training_dataset/yolo/split_summary.json
```

Open these CSVs in a spreadsheet or notebook:

```text
training_dataset/yolo/class_distribution.csv
training_dataset/yolo/source_distribution.csv
training_dataset/yolo/object_size_distribution.csv
```

Block training if:

- Any defense parent class has near-zero validation labels.
- `dam`, `recreation`, or `water` appear as active labels unless intentionally enabled.
- One source dataset dominates most validation labels.
- Tiny/small vehicles or ships are absent from validation.

### 4. Retrain Baselines

Run at least two comparable baselines:

```bash
python inference/train_model.py \
  --data training_dataset/yolo/data.yaml \
  --base-model yolov8s-obb.pt \
  --epochs 100 \
  --imgsz 1024 \
  --batch auto \
  --device auto \
  --name geoint_yolov8s_1024_defense

python inference/train_model.py \
  --data training_dataset/yolo/data.yaml \
  --base-model yolov8m-obb.pt \
  --epochs 100 \
  --imgsz 1024 \
  --batch auto \
  --device auto \
  --name geoint_yolov8m_1024_defense \
  --no-promote
```

Use `--imgsz 1280` only if GPU memory allows.

### 5. Evaluate Promotion

Do not promote by all-class mAP alone.

Promotion requires:

- Defense-core recall above `0.525`.
- False positives on dam/infrastructure hard negatives materially reduced.
- Small vehicle and ship recall improved on the fixed failure benchmark.
- No numeric HRSC classes in model names.
- GeoJSON output includes non-null `review_status`, `parent_class`, `original_class`, and `coverage_fraction`.
- Large raster ingest reports full coverage or an explicit partial coverage fraction.

### 6. Tune Threshold Profiles

After validation, update thresholds from measured per-class curves:

- Keep `recall_review` low enough to surface analyst candidates.
- Tune `balanced` for day-to-day map display.
- Tune `high_precision` for high-confidence alerts.

The active profile is controlled by:

```text
DETECTION_THRESHOLD_PROFILE=recall_review
```

Optional overrides:

```text
GLOBAL_CONFIDENCE_FLOOR=0.10
HIGH_CONFIDENCE_THRESHOLD=0.55
ENABLED_PARENT_CLASSES=aircraft,ship,vehicle,military_vehicle,storage_tank,bridge,harbor,airfield,building,infrastructure
DISABLED_PARENT_CLASSES=dam,recreation,water,unknown
PER_CLASS_CONFIDENCE_OVERRIDES={"vehicle":0.12,"ship":0.14}
```

### 7. Run End-To-End Ingest Validation

After restarting compose:

```bash
docker compose up -d --build
curl http://localhost:8002/health
curl http://localhost:8080/api/health
```

Then ingest a known optical raster and verify:

- Map shows detections as parent classes.
- Popup shows original class.
- Dam/recreation false positives are absent or downgraded.
- Detection detail shows review status and threshold profile.
- Upload job metadata includes inference summary and coverage.

## Known Limitations

- The current model weights were not retrained with the new taxonomy. Existing model outputs are mapped at inference time.
- Full robustness depends on regenerating the dataset and training against the collapsed taxonomy plus hard negatives.
- The promotion gate uses final aggregate recall because class-wise validation artifacts are not available locally yet.
- Certified adversarial robustness and SAR/thermal/hyperspectral fusion remain out of scope for this optical-only build.
