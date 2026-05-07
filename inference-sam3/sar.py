from __future__ import annotations

import io

import numpy as np
import rasterio


TERRAMIND_S1_SIZE = 224
SAR_DB_FLOOR = -30.0
SAR_DB_CEIL = 0.0


def decode_s1grd(payload: bytes) -> np.ndarray:
    with rasterio.open(io.BytesIO(payload)) as src:
        if src.count < 2:
            raise ValueError(f"Expected 2 SAR bands (VV,VH), got {src.count}")
        arr = src.read(indexes=[1, 2]).astype(np.float32)
    if float(np.nanmin(arr)) >= 0.0:
        arr = 10.0 * np.log10(np.maximum(arr, 1e-6))
    arr = np.clip(arr, SAR_DB_FLOOR, SAR_DB_CEIL)
    return ((arr - SAR_DB_FLOOR) / (SAR_DB_CEIL - SAR_DB_FLOOR)).astype(np.float32)


def resize_to_terramind(arr_norm: np.ndarray) -> np.ndarray:
    import cv2

    hwc = arr_norm.transpose(1, 2, 0)
    resized = cv2.resize(hwc, (TERRAMIND_S1_SIZE, TERRAMIND_S1_SIZE), interpolation=cv2.INTER_LINEAR)
    return resized.transpose(2, 0, 1).astype(np.float32)
