# FAIR1M-OBB weights drop-in

Operator-baked FAIR1M-2.0 fine-grained OBB checkpoint goes here as
`yolo11m-obb-fair1m.pt`. The `.pt` file is `.gitignore`d (~80 MB);
only this README and `.gitkeep` are tracked.

See [../../../docs/operations/fair1m-bake.md](../../../docs/operations/fair1m-bake.md)
for the full workflow (dataset download, conversion, training, bake-and-rsync
into the `inference_weights` shared volume).

When weights are absent the [inference-sam3/fair1m_obb.py](../../../inference-sam3/fair1m_obb.py)
runner returns `{model: None}` and the layer silently contributes zero
candidates — this is the safe default state.
