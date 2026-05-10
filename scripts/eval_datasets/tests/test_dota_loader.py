"""
Smoke tests for:
  - scripts/fetch_eval_datasets.generate_synthetic_dota()
  - scripts/eval_datasets/dota.iter_samples()
  - scripts/eval_datasets/dota.iter_dota()

Run with:
    cd <repo_root>
    python -m pytest scripts/eval_datasets/tests/ -q
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure scripts/ is importable without installation
_SCRIPTS_DIR = Path(__file__).resolve().parents[3]  # repo root
_SCRIPTS_SUBDIR = _SCRIPTS_DIR / "scripts"
for _p in (str(_SCRIPTS_DIR), str(_SCRIPTS_SUBDIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fetch_eval_datasets as _fetcher  # noqa: E402
from eval_datasets.dota import iter_dota, iter_samples  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic_dir(tmp_path_factory):
    """Generate synthetic DOTA chips into a fresh temp directory once per module."""
    d = tmp_path_factory.mktemp("dota_val")
    _fetcher.generate_synthetic_dota(output_dir=d, n_chips=10)
    return d


# ---------------------------------------------------------------------------
# test_fetch_creates_chips
# ---------------------------------------------------------------------------

def test_fetch_creates_chips(synthetic_dir):
    """generate_synthetic_dota() must create chip_000.png … chip_009.png and labels.json."""
    # Check chip_000.png
    chip0 = synthetic_dir / "chip_000.png"
    assert chip0.exists(), f"chip_000.png not found in {synthetic_dir}"
    assert chip0.stat().st_size > 0, "chip_000.png is empty"

    # Check all 10 chips
    for i in range(10):
        chip = synthetic_dir / f"chip_{i:03d}.png"
        assert chip.exists(), f"{chip.name} missing"

    # Check labels.json is valid JSON and has the right structure
    labels_path = synthetic_dir / "labels.json"
    assert labels_path.exists(), "labels.json not found"
    with labels_path.open() as fh:
        records = json.load(fh)
    assert isinstance(records, list), "labels.json root must be a list"
    assert len(records) == 10, f"Expected 10 records, got {len(records)}"

    # Validate structure of the first record
    first = records[0]
    assert "chip" in first, f"Record missing 'chip' key: {first}"
    assert "boxes" in first, f"Record missing 'boxes' key: {first}"
    assert isinstance(first["boxes"], list), "'boxes' must be a list"
    for box in first["boxes"]:
        assert "label" in box, f"Box missing 'label': {box}"
        assert "bbox_xyxy" in box, f"Box missing 'bbox_xyxy': {box}"
        assert len(box["bbox_xyxy"]) == 4, f"bbox_xyxy must have 4 elements: {box}"


def test_fetch_is_idempotent(synthetic_dir):
    """Calling generate_synthetic_dota() again must not raise and must not overwrite."""
    mtime_before = (synthetic_dir / "chip_000.png").stat().st_mtime
    _fetcher.generate_synthetic_dota(output_dir=synthetic_dir, n_chips=10)
    mtime_after = (synthetic_dir / "chip_000.png").stat().st_mtime
    assert mtime_before == mtime_after, "Idempotent call must not overwrite existing chips"


# ---------------------------------------------------------------------------
# test_iter_samples_yields_correct_shape
# ---------------------------------------------------------------------------

def test_iter_samples_yields_correct_shape(synthetic_dir):
    """iter_samples() must yield at least 5 samples with the required keys and types."""
    samples = list(iter_samples(synthetic_dir))
    assert len(samples) >= 5, f"Expected at least 5 samples, got {len(samples)}"

    required_keys = {"chip_path", "chip_bytes", "modality", "prompts", "ground_truth"}
    for i, sample in enumerate(samples):
        missing = required_keys - sample.keys()
        assert not missing, f"Sample {i} missing keys: {missing}"

        assert sample["modality"] == "rgb", (
            f"Sample {i}: expected modality='rgb', got {sample['modality']!r}"
        )

        assert isinstance(sample["chip_bytes"], bytes), (
            f"Sample {i}: chip_bytes must be bytes"
        )
        assert len(sample["chip_bytes"]) > 0, f"Sample {i}: chip_bytes is empty"

        assert isinstance(sample["ground_truth"], list), (
            f"Sample {i}: ground_truth must be a list"
        )
        assert len(sample["ground_truth"]) > 0, (
            f"Sample {i}: ground_truth must be non-empty"
        )

        for box in sample["ground_truth"]:
            assert "label" in box, f"Sample {i}: GT box missing 'label': {box}"
            assert isinstance(box["label"], str), (
                f"Sample {i}: GT box 'label' must be str"
            )
            assert "bbox_xyxy" in box, f"Sample {i}: GT box missing 'bbox_xyxy': {box}"
            bbox = box["bbox_xyxy"]
            assert isinstance(bbox, list) and len(bbox) == 4, (
                f"Sample {i}: bbox_xyxy must be a list of 4 elements, got {bbox!r}"
            )
            assert all(isinstance(v, (int, float)) for v in bbox), (
                f"Sample {i}: bbox_xyxy elements must be numeric, got {bbox!r}"
            )

        assert isinstance(sample["prompts"], list), (
            f"Sample {i}: prompts must be a list"
        )
        # prompts = unique labels from ground_truth
        gt_labels = {box["label"] for box in sample["ground_truth"]}
        assert set(sample["prompts"]) == gt_labels, (
            f"Sample {i}: prompts {sample['prompts']} != GT labels {gt_labels}"
        )


def test_chip_png_bytes_are_png(synthetic_dir):
    """chip_bytes must start with the PNG magic bytes."""
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    for sample in iter_samples(synthetic_dir):
        assert sample["chip_bytes"][:8] == PNG_MAGIC, (
            f"{sample['chip_path'].name}: chip_bytes do not look like a PNG"
        )


# ---------------------------------------------------------------------------
# test_iter_dota_with_synthetic_labels
# ---------------------------------------------------------------------------

def test_iter_dota_with_synthetic_labels(tmp_path):
    """Create a minimal labels.json + one PNG and verify iter_dota yields it correctly."""
    try:
        from PIL import Image
        img = Image.new("RGB", (64, 64), color=(128, 128, 128))
    except ImportError:
        import struct, zlib  # noqa: E401
        # Minimal 64x64 grey PNG without Pillow
        def _make_png(w, h):
            def chunk(tag, data):
                c = struct.pack(">I", len(data)) + tag + data
                return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            raw = b""
            for _ in range(h):
                raw += b"\x00" + bytes([128] * w * 3)
            compressed = zlib.compress(raw)
            return (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", compressed)
                + chunk(b"IEND", b"")
            )
        chips_dir = tmp_path / "chips"
        chips_dir.mkdir()
        (chips_dir / "0001.png").write_bytes(_make_png(64, 64))
        img = None

    if img is not None:
        chips_dir = tmp_path / "chips"
        chips_dir.mkdir()
        img.save(chips_dir / "0001.png", format="PNG")

    # Write a minimal labels.json
    labels = [
        {
            "chip_file": "chips/0001.png",
            "modality": "rgb",
            "annotations": [
                {"label": "plane", "bbox_xyxy": [10, 10, 50, 50]},
            ],
        }
    ]
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps(labels))

    # Run iter_dota and collect results
    results = list(iter_dota(labels_path=str(labels_path)))

    assert len(results) == 1, f"Expected exactly 1 tuple, got {len(results)}"

    chip_bytes, modality, prompts, ground_truth = results[0]

    assert isinstance(chip_bytes, bytes), "chip_bytes must be bytes"
    assert len(chip_bytes) > 0, "chip_bytes must not be empty"
    assert chip_bytes[:8] == b"\x89PNG\r\n\x1a\n", "chip_bytes must be a valid PNG"

    assert modality == "rgb", f"Expected modality='rgb', got {modality!r}"

    assert prompts == ["plane"], f"Expected prompts==['plane'], got {prompts!r}"

    assert len(ground_truth) == 1, f"Expected 1 GT box, got {len(ground_truth)}"
    assert ground_truth[0]["label"] == "plane", (
        f"Expected label='plane', got {ground_truth[0]['label']!r}"
    )
    assert ground_truth[0]["bbox_xyxy"] == [10, 10, 50, 50], (
        f"Expected bbox_xyxy=[10,10,50,50], got {ground_truth[0]['bbox_xyxy']!r}"
    )
