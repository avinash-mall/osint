# Recipe — Add a New Detection Model

## When this applies

You want to add another detector (e.g. a specialist for ships, oriented-box variant, new modality). It will run inside the `inference-sam3` service.

## Steps

1. **Add weights to [inference-sam3/MODEL_MANIFEST.json](../../inference-sam3/MODEL_MANIFEST.json)** with `{repo, revision (SHA), gated, files}` or `{local}` for bundled weights. Pin the revision SHA, not a branch name. See [inference/model-manifest.md](../inference/model-manifest.md).

2. **Create the runner module:** `inference-sam3/<name>.py` that exposes:
   - `load(device) -> bundle: dict`
   - `run(bundle, image, prompts) -> list[(mask, bbox_xyxy, score, label)]` — matching the existing detectors' signature so [fusion.py](../../inference-sam3/fusion.py) can ingest results.
   - `model_versions(bundle) -> dict` — surfaced in `/health`.

3. **Register a load flag** `SAM3_LOAD_<NAME>` in [inference-sam3/main.py](../../inference-sam3/main.py). Wire it into `_build_component` (`#L464`) and `_load_profile` (`#L521`).

4. **Add to the profile lists** in `_load_profile`. Decide whether it belongs to `imagery`, `fmv`, or both.

5. **Add an enabled-layer toggle** so the operator can request it per-detect. Update the worker's `enabled_layers` dispatch (see [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)).

6. **Verify with the benchmark harness** before promoting:

   ```bash
   python scripts/compare_inference_layers.py \
     --url http://172.18.0.2:8001 \
     --slice dota --max-chips 30 --repeats 3 \
     --output bench/new_model_check.md \
     --layers "your_new_layer"
   ```

   If the new model only **adds false positives** without adding true positives, **do not ship**. See [decisions/removed-defence-yolo.md](../decisions/removed-defence-yolo.md) for the cautionary tale (1297 FPs / 0 TPs).

7. **Write a doc** at `docs/inference/<your-model>.md` following the template in [docs/README.md](../README.md). Link from [docs/INDEX.txt](../INDEX.txt).

8. **Update the env reference** in [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Common pitfalls

- **Forgetting the gate.** If the new model competes with DOTA-OBB or SAM3 on common-vocab prompts, you may need a gate similar to [grounding-dino-gate.md](../inference/grounding-dino-gate.md). Otherwise NMS will silently degrade the better detector — see [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md).
- **Loading by default.** Set `SAM3_LOAD_<NAME>=1` only if the model is universally net-positive. Otherwise default `0` and document the tradeoff.

## Cross-references

- [inference/model-manifest.md](../inference/model-manifest.md)
- [inference/service-overview.md](../inference/service-overview.md)
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md)
- [decisions/removed-defence-yolo.md](../decisions/removed-defence-yolo.md)
