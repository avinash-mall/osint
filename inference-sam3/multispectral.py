from __future__ import annotations

import io

import numpy as np
import rasterio


PRITHVI_CONSTANT_SCALE = 0.0001
PRITHVI_SIZE = 224


def decode_hls6(payload: bytes) -> np.ndarray:
    with rasterio.open(io.BytesIO(payload)) as src:
        if src.count < 6:
            raise ValueError(f"Expected at least 6 HLS bands, got {src.count}")
        arr = src.read(indexes=list(range(1, 7))).astype(np.float32)
    return arr * PRITHVI_CONSTANT_SCALE if float(np.nanmean(arr)) > 1.0 else arr


def hls_to_rgb_preview(arr_reflectance: np.ndarray) -> np.ndarray:
    rgb = np.nan_to_num(arr_reflectance[[2, 1, 0]].astype(np.float32), nan=0.0)
    p2, p98 = np.percentile(rgb, [2, 98], axis=(1, 2), keepdims=True)
    rgb = np.clip((rgb - p2) / np.maximum(p98 - p2, 1e-6), 0.0, 1.0)
    return (rgb * 255).astype(np.uint8).transpose(1, 2, 0)


def resize_to_prithvi(arr_reflectance: np.ndarray) -> np.ndarray:
    import cv2

    hwc = arr_reflectance.transpose(1, 2, 0)
    resized = cv2.resize(hwc, (PRITHVI_SIZE, PRITHVI_SIZE), interpolation=cv2.INTER_LINEAR)
    return resized.transpose(2, 0, 1).astype(np.float32)


def pad_to_window(arr_reflectance: np.ndarray, window_size: int = 512) -> np.ndarray:
    h, w = arr_reflectance.shape[-2:]
    pad_h = (window_size - (h % window_size)) % window_size
    pad_w = (window_size - (w % window_size)) % window_size
    return np.pad(arr_reflectance, ((0, 0), (0, pad_h), (0, pad_w)), mode="reflect")
