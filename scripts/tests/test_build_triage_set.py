"""
Smoke tests for scripts/build_triage_set.py.

Exercises --dry-run against a synthetic --data-dir of fake COG files, plus the
real on-disk happy-path against an even smaller synthetic dataset.

Run with:
    cd <repo_root>
    python -m pytest scripts/tests/test_build_triage_set.py -q
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest
import yaml

# Ensure scripts/ is importable
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_triage_set  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_bytes(width: int = 32, height: int = 32) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        head = struct.pack(">I", len(data)) + tag + data
        return head + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes([200] * width * 3)
    compressed = zlib.compress(raw)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _make_fake_cog(path: Path, size: int = 128) -> None:
    """Write a real (tiny) GeoTIFF via rasterio so the script can open it."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    arr = np.full((3, size, size), 180, dtype=np.uint8)
    transform = from_origin(0.0, float(size), 1.0, 1.0)
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "count": 3,
        "width": size,
        "height": size,
        "crs": "EPSG:4326",
        "transform": transform,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)


@pytest.fixture
def fake_data_dir(tmp_path: Path) -> Path:
    """Populate a fake processed/ directory with three COG files."""
    data_dir = tmp_path / "imagery" / "processed"
    data_dir.mkdir(parents=True)
    for upload_id in ("aaaa", "bbbb", "cccc"):
        _make_fake_cog(data_dir / f"{upload_id}_test_cog.tif")
    return data_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dry_run_does_not_write_anything(fake_data_dir: Path, tmp_path: Path) -> None:
    """--dry-run reports what it would do but never touches the filesystem."""
    out_dir = tmp_path / "triage_out"
    # out_dir intentionally absent before the call

    exit_code = build_triage_set.main(
        [
            "--dry-run",
            "--source", "data-dir",
            "--data-dir", str(fake_data_dir),
            "--out", str(out_dir),
            "--max-uploads", "2",
            "--chips-per-upload", "1",
        ]
    )

    assert exit_code == 0, f"Non-zero exit code: {exit_code}"
    assert not out_dir.exists(), (
        f"--dry-run must not create the output directory ({out_dir})"
    )


def test_data_dir_mode_writes_chips_and_annotations(
    fake_data_dir: Path, tmp_path: Path
) -> None:
    """Happy path: data-dir mode writes chips, sidecars, README and YAML."""
    out_dir = tmp_path / "triage_out"

    exit_code = build_triage_set.main(
        [
            "--source", "data-dir",
            "--data-dir", str(fake_data_dir),
            "--out", str(out_dir),
            "--max-uploads", "2",
            "--chips-per-upload", "1",
        ]
    )
    assert exit_code == 0

    assert (out_dir / "annotations.yaml").exists()
    assert (out_dir / "README.md").exists()
    chips_dir = out_dir / "chips"
    assert chips_dir.exists()

    pngs = sorted(chips_dir.glob("*.png"))
    sidecars = sorted(chips_dir.glob("*.json"))

    # 2 uploads x 1 chip each
    assert len(pngs) == 2, f"Expected 2 PNGs, got {len(pngs)}"
    assert len(sidecars) == 2, f"Expected 2 JSON sidecars, got {len(sidecars)}"

    for png in pngs:
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    doc = yaml.safe_load((out_dir / "annotations.yaml").read_text())
    assert "chips" in doc
    assert len(doc["chips"]) == 2
    for row in doc["chips"]:
        assert "chip" in row
        assert "sensor" in row
        assert "expected_labels" in row
        assert row["expected_labels"] == []  # placeholder for the analyst


def test_pick_recent_uploads_respects_mtime(fake_data_dir: Path) -> None:
    """The helper picks the most-recent N COG files by mtime."""
    import os
    # Force a known mtime ordering: aaaa oldest, cccc newest
    cog_files = sorted(fake_data_dir.glob("*_cog.tif"))
    for idx, cog in enumerate(cog_files):
        ts = 1_700_000_000 + idx * 1000
        os.utime(cog, (ts, ts))

    picked = build_triage_set._pick_recent_uploads(fake_data_dir, max_uploads=2)
    assert len(picked) == 2
    # Most recent first
    assert picked[0].name == "cccc_test_cog.tif"
    assert picked[1].name == "bbbb_test_cog.tif"


def test_dry_run_reports_planned_chip_count(
    fake_data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run prints the chip count it would write."""
    out_dir = tmp_path / "triage_out"

    exit_code = build_triage_set.main(
        [
            "--dry-run",
            "--source", "data-dir",
            "--data-dir", str(fake_data_dir),
            "--out", str(out_dir),
            "--max-uploads", "3",
            "--chips-per-upload", "2",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    # 3 uploads; the 128 px fixtures are smaller than the 1008 px chip size,
    # so the M-2 cap kicks in and each upload emits a single chip (3 total).
    assert "3" in captured.out or "3" in captured.err


def test_empty_data_dir_exits_zero_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty data-dir prints a warning and exits without crashing."""
    empty_data_dir = tmp_path / "empty"
    empty_data_dir.mkdir()
    out_dir = tmp_path / "triage_out"

    exit_code = build_triage_set.main(
        [
            "--dry-run",
            "--source", "data-dir",
            "--data-dir", str(empty_data_dir),
            "--out", str(out_dir),
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "no" in (captured.out + captured.err).lower()


def test_colliding_upload_ids_are_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two COGs whose pre-underscore prefix matches must not overwrite each other."""
    import logging

    data_dir = tmp_path / "imagery" / "processed"
    data_dir.mkdir(parents=True)
    # Both COGs share the prefix "dup" before the first underscore — under the
    # old behaviour their chips would map to identical filenames and the second
    # COG would silently overwrite the first.
    _make_fake_cog(data_dir / "dup_first_cog.tif")
    _make_fake_cog(data_dir / "dup_second_cog.tif")

    out_dir = tmp_path / "triage_out"
    caplog.set_level(logging.WARNING, logger="build_triage_set")

    exit_code = build_triage_set.main(
        [
            "--source", "data-dir",
            "--data-dir", str(data_dir),
            "--out", str(out_dir),
            "--max-uploads", "5",
            "--chips-per-upload", "1",
        ]
    )
    assert exit_code == 0

    # Exactly one chip survives — the collider was skipped, not overwritten.
    pngs = sorted((out_dir / "chips").glob("*.png"))
    assert len(pngs) == 1, f"Expected 1 PNG (collision skipped), got {len(pngs)}"
    assert pngs[0].name == "dup_0.png"

    # The warning trail names the colliding upload id.
    collision_msgs = [
        rec.message for rec in caplog.records
        if "collides with previous" in rec.message
    ]
    assert collision_msgs, "Expected a collision warning to be logged"
    assert "dup" in collision_msgs[0]
