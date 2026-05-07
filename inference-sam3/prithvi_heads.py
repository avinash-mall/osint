from __future__ import annotations

import numpy as np


PRITHVI_FLOOD_ID = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11"
PRITHVI_BURN_ID = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars"
PRITHVI_CROP_ID = "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification"
CROP_CLASS_NAMES = [
    "natural_vegetation",
    "forest",
    "corn",
    "soybeans",
    "wetlands",
    "developed_barren",
    "open_water",
    "winter_wheat",
    "alfalfa",
    "fallow_idle_cropland",
    "cotton",
    "sorghum",
    "other",
]


def load_all(device: str):
    from terratorch.registry import BACKBONE_REGISTRY

    return {
        "flood": BACKBONE_REGISTRY.build(PRITHVI_FLOOD_ID).to(device).eval(),
        "burn": BACKBONE_REGISTRY.build(PRITHVI_BURN_ID).to(device).eval(),
        "crop": BACKBONE_REGISTRY.build(PRITHVI_CROP_ID).to(device).eval(),
        "device": device,
    }


def run_all(prithvi_bundle, chip6_full: np.ndarray, target_hw: tuple[int, int], chip6_temporal_3: np.ndarray | None = None) -> dict[str, np.ndarray]:
    if prithvi_bundle is None:
        return {}
    import cv2
    import torch
    import multispectral

    h, w = target_hw
    overlays: dict[str, np.ndarray] = {}
    x = torch.from_numpy(multispectral.resize_to_prithvi(chip6_full)).unsqueeze(0).to(prithvi_bundle["device"])
    with torch.inference_mode():
        flood_logits = prithvi_bundle["flood"](x)
        flood_mask = flood_logits.argmax(1)[0].cpu().numpy() == 1
        overlays["water"] = cv2.resize(flood_mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)

        burn_logits = prithvi_bundle["burn"](x)
        burn_mask = burn_logits.argmax(1)[0].cpu().numpy() == 1
        overlays["burn_scar"] = cv2.resize(burn_mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)

        if chip6_temporal_3 is not None:
            stacked = np.stack(
                [multispectral.resize_to_prithvi(chip6_temporal_3[:, t]) for t in range(3)],
                axis=1,
            )
            xt = torch.from_numpy(stacked).unsqueeze(0).to(prithvi_bundle["device"])
            crop_logits = prithvi_bundle["crop"](xt)
            crop_map = crop_logits.argmax(1)[0].cpu().numpy().astype(np.int16)
            overlays["crop"] = cv2.resize(crop_map, (w, h), interpolation=cv2.INTER_NEAREST)
    return overlays


def crop_class_name(label_map: np.ndarray, bbox_xyxy: list[float]) -> str:
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_xyxy)
    region = label_map[max(0, y1):y2, max(0, x1):x2]
    if region.size == 0:
        return "unknown"
    cls_id = int(np.bincount(region.flatten().astype(np.int64)).argmax())
    return CROP_CLASS_NAMES[cls_id] if 0 <= cls_id < len(CROP_CLASS_NAMES) else "unknown"
