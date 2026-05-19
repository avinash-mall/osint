# Why SAR Detections Are Confidence-Capped at 0.85

## Decision

Every detection on a `modality=sar` chip has its `confidence` **hard-capped at `SAM3_SAR_CONF_CAP=0.85`** and is flagged `sar_proxy=true`, `review_status="review_candidate"`.

## Why

SAR is not segmented directly. Sentinel-1 GRD comes in as 2-band VV/VH (dB-clipped to [-30, 0], then linearly stretched to [0, 1]). [TerraMind v1](../inference/terramind-sar-synthesis.md) generates a **synthetic S2-L2A optical preview** from the SAR bands, and SAM3 runs on that synthetic RGB.

The synthetic preview is **not** ground truth. It's a plausible optical reconstruction conditioned on the SAR backscatter. Two consequences:

1. **A confident SAM3 detection on a synthetic image is not as reliable as one on a real optical chip.** SAM3 sees what TerraMind invented — bright spots in the SAR map mean "something here," but the exact shape is a generation.
2. **Operators must review SAR detections by hand** with the actual SAR raster before acting on them.

The cap and flags make this visible in the UI (SAR detections appear in the operator's review queue) and in downstream automation (candidate-link scoring weights SAR detections lower).

## Implementation

[inference-sam3/main.py](../../inference-sam3/main.py) applies the cap inside the `/detect` SAR branch:

```python
det.confidence = min(det.confidence, SAM3_SAR_CONF_CAP)
det.sar_proxy = True
det.review_status = "review_candidate"
```

Override `SAM3_SAR_CONF_CAP=1.0` only for experiments — production stays at 0.85.

## Alternative considered

A separate SAR-native ship detector (CFAR — see [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md)) runs independently of SAM3 and does not go through TerraMind. CFAR detections are *not* SAR-proxy — they're real backscatter peaks. They appear alongside SAM3-via-TerraMind detections in the imagery pipeline. Don't conflate.

## Cross-references

- [inference/terramind-sar-synthesis.md](../inference/terramind-sar-synthesis.md)
- [inference/sar-bands.md](../inference/sar-bands.md)
- [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md)
