"""
Smoke tests for scripts/eval_datasets/triage.py.

Run with:
    cd <repo_root>
    python -m pytest scripts/eval_datasets/tests/test_triage.py -q
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest
import yaml

# Ensure scripts/ is importable without installation
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_SUBDIR = _REPO_ROOT / "scripts"
for _p in (str(_REPO_ROOT), str(_SCRIPTS_SUBDIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eval_datasets.triage import iter_triage  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png(width: int = 32, height: int = 32) -> bytes:
    """Return raw PNG bytes for a solid-grey image. Avoids Pillow dependency."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        head = struct.pack(">I", len(data)) + tag + data
        return head + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes([128] * width * 3)
    compressed = zlib.compress(raw)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _write_chip(chips_dir: Path, name: str, sensor: str, modality: str,
                w: int = 32, h: int = 32) -> None:
    """Write a chip PNG plus its sidecar JSON."""
    png_bytes = _make_png(w, h)
    (chips_dir / name).write_bytes(png_bytes)

    meta = {
        "modality": modality,
        "sensor": sensor,
        "branch": "default",
        "source_pass": "test-pass",
        "width": w,
        "height": h,
    }
    json_name = Path(name).with_suffix(".json").name
    (chips_dir / json_name).write_text(json.dumps(meta))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def triage_dir(tmp_path: Path) -> Path:
    """Build a minimal triage set on disk and return its root directory."""
    chips_dir = tmp_path / "chips"
    chips_dir.mkdir()

    _write_chip(chips_dir, "upload-a_0.png", sensor="optical", modality="rgb")
    _write_chip(chips_dir, "upload-b_0.png", sensor="optical", modality="rgb")
    _write_chip(chips_dir, "upload-c_0.png", sensor="sar", modality="sar")

    annotations = {
        "chips": [
            {
                "chip": "upload-a_0.png",
                "sensor": "optical",
                "expected_labels": ["aircraft", "naval"],
            },
            {
                "chip": "upload-b_0.png",
                "sensor": "optical",
                "expected_labels": [],
            },
            {
                "chip": "upload-c_0.png",
                "sensor": "sar",
                "expected_labels": ["naval"],
            },
        ]
    }
    (tmp_path / "annotations.yaml").write_text(yaml.safe_dump(annotations))
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_iter_triage_yields_rgb_chips_by_default(triage_dir: Path) -> None:
    """Default rgb_only=True filters out the SAR chip."""
    results = list(iter_triage(triage_dir))

    # 2 RGB chips, SAR chip filtered out
    assert len(results) == 2, f"Expected 2 RGB tuples, got {len(results)}"

    chip_names_seen = set()
    for chip_bytes, modality, prompts, ground_truth in results:
        assert isinstance(chip_bytes, bytes)
        assert chip_bytes[:8] == b"\x89PNG\r\n\x1a\n", "chip_bytes must be valid PNG"
        assert modality == "rgb"
        assert isinstance(prompts, list)
        assert isinstance(ground_truth, list)
        for gt in ground_truth:
            assert "label" in gt
            assert "bbox_xyxy" in gt
            bbox = gt["bbox_xyxy"]
            assert len(bbox) == 4
            assert bbox[0] == 0 and bbox[1] == 0
            # bbox covers the whole chip
            assert bbox[2] > 0 and bbox[3] > 0
        # Track which chip we saw via prompts (only chip-a has prompts)
        if prompts == ["aircraft", "naval"]:
            chip_names_seen.add("a")
            assert len(ground_truth) == 2
        elif prompts == []:
            chip_names_seen.add("b")
            assert ground_truth == []

    assert chip_names_seen == {"a", "b"}, (
        f"Expected chips a and b only, got {chip_names_seen}"
    )


def test_iter_triage_include_non_rgb(triage_dir: Path) -> None:
    """rgb_only=False yields every chip including SAR."""
    results = list(iter_triage(triage_dir, rgb_only=False))
    assert len(results) == 3, f"Expected 3 tuples, got {len(results)}"

    modalities = sorted(modality for _b, modality, _p, _gt in results)
    assert modalities == ["rgb", "rgb", "sar"]


def test_iter_triage_prompts_match_expected_labels(triage_dir: Path) -> None:
    """prompts list reflects expected_labels from annotations.yaml."""
    by_prompts: dict[tuple[str, ...], list] = {}
    for chip_bytes, modality, prompts, gt in iter_triage(triage_dir, rgb_only=False):
        by_prompts[tuple(prompts)] = gt

    assert ("aircraft", "naval") in by_prompts
    assert () in by_prompts  # the empty-expected chip
    assert ("naval",) in by_prompts


def test_iter_triage_missing_yaml_raises(tmp_path: Path) -> None:
    """Pointing at a directory without annotations.yaml raises FileNotFoundError."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError) as exc_info:
        list(iter_triage(empty))
    # Helpful error message points at the build script
    assert "build_triage_set.py" in str(exc_info.value)


def test_iter_triage_missing_chip_raises(tmp_path: Path) -> None:
    """A chip listed in YAML but missing on disk raises FileNotFoundError."""
    chips_dir = tmp_path / "chips"
    chips_dir.mkdir()
    annotations = {
        "chips": [
            {
                "chip": "does-not-exist.png",
                "sensor": "optical",
                "expected_labels": ["aircraft"],
            }
        ]
    }
    (tmp_path / "annotations.yaml").write_text(yaml.safe_dump(annotations))

    with pytest.raises(FileNotFoundError) as exc_info:
        list(iter_triage(tmp_path))
    assert "does-not-exist.png" in str(exc_info.value)
