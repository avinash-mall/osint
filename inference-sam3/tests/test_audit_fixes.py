"""Regression tests for the 2026-06-12 inference audit fixes.

Covers the CPU-testable fixes:

* resolve_prompts caps the explicit ``text_prompts`` branch (the production
  path from the worker) — SAM3_MAX_PROMPTS_PER_REQUEST / metadata.max_prompts
  were dead knobs on that branch.
* sar.decode_s1grd neutralises NaN nodata (S1 GRD swath edges) instead of
  letting it smear through clip/resize/percentile into a garbage black chip.
* fusion's detection-policy loader rejects empty / symbol-less candidates
  (the 0-byte bind-mount anchor) and falls through to the next path.

See docs/decisions/audit-fixes-inference-2026-06-12.md.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest

import main
import sar
import fusion


# ---------------------------------------------------------------------------
# Finding 9: explicit text_prompts branch is capped.
# ---------------------------------------------------------------------------


def test_explicit_text_prompts_capped_by_max_prompts_override():
    prompts = [f"class {i}" for i in range(40)]
    out = main.resolve_prompts({"text_prompts": prompts, "max_prompts": 5})
    assert out == prompts[:5]


def test_explicit_text_prompts_capped_by_env_default():
    prompts = [f"class {i}" for i in range(main.SAM3_MAX_IMAGE_PROMPTS + 20)]
    out = main.resolve_prompts({"text_prompts": prompts})
    assert len(out) == main.SAM3_MAX_IMAGE_PROMPTS
    assert out == prompts[: main.SAM3_MAX_IMAGE_PROMPTS]


def test_explicit_text_prompts_under_cap_unchanged():
    out = main.resolve_prompts({"text_prompts": ["ship", "vehicle"]})
    assert out == ["ship", "vehicle"]


def test_explicit_empty_text_prompts_still_rejected():
    with pytest.raises(ValueError):
        main.resolve_prompts({"text_prompts": []})


# ---------------------------------------------------------------------------
# Finding 8: NaN nodata neutralised in decode_s1grd.
# ---------------------------------------------------------------------------


def _geotiff_bytes(arr: np.ndarray) -> bytes:
    import rasterio
    from rasterio.io import MemoryFile

    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            height=arr.shape[1],
            width=arr.shape[2],
            count=arr.shape[0],
            dtype="float32",
        ) as dst:
            dst.write(arr)
        return mem.read()


def test_decode_s1grd_nan_in_linear_power_maps_to_floor():
    arr = np.full((2, 8, 8), 0.5, dtype=np.float32)  # linear power (>= 0)
    arr[:, :2, :] = np.nan  # swath-edge nodata
    out = sar.decode_s1grd(_geotiff_bytes(arr))
    assert not np.isnan(out).any()
    assert np.all((out >= 0.0) & (out <= 1.0))
    # nodata → 0 linear → dB floor → normalised 0.0 (black, not garbage).
    assert np.all(out[:, :2, :] == 0.0)


def test_decode_s1grd_nan_in_db_domain_maps_to_floor():
    arr = np.full((2, 8, 8), -12.0, dtype=np.float32)  # already in dB (< 0)
    arr[:, :, :3] = np.nan
    out = sar.decode_s1grd(_geotiff_bytes(arr))
    assert not np.isnan(out).any()
    assert np.all(out[:, :, :3] == 0.0)


# ---------------------------------------------------------------------------
# Finding 5: policy loader rejects empty / symbol-less candidates.
# ---------------------------------------------------------------------------


def test_policy_loader_skips_empty_file_then_loads_next(tmp_path, monkeypatch):
    empty = tmp_path / "empty_policy.py"
    empty.write_text("")
    good = tmp_path / "good_policy.py"
    good.write_text("def parent_class_for_label(label):\n    return 'tested_' + str(label)\n")
    monkeypatch.setattr(fusion, "_POLICY_CANDIDATES", (empty, good))
    fn = fusion._load_parent_class_for_label()
    assert fn is not None
    assert fn("ship") == "tested_ship"


def test_policy_loader_skips_symbolless_candidate(tmp_path, monkeypatch):
    no_symbol = tmp_path / "no_symbol.py"
    no_symbol.write_text("X = 1\n")
    monkeypatch.setattr(fusion, "_POLICY_CANDIDATES", (no_symbol,))
    assert fusion._load_parent_class_for_label() is None


def test_policy_loader_returns_none_when_no_candidate_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fusion, "_POLICY_CANDIDATES", (tmp_path / "missing.py",)
    )
    assert fusion._load_parent_class_for_label() is None


def test_policy_candidates_order_prefers_container_mount():
    parents = [c.parent for c in fusion._POLICY_CANDIDATES]
    # 1) /app mount point (inference-sam3/ on the dev host), 2) backend tree.
    assert parents[0] == Path(fusion.__file__).resolve().parent
    assert parents[1].name == "backend"
