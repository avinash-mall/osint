"""Unit tests for backend/scripts/stage_dota_chips.py.

No PostGIS or HTTP dependencies — pure file I/O against tmp_path with synthetic
chips and a tiny labels.json. Mirrors the synthetic-fixture style of
test_reference_platform_baker.py without the integration marker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

# scripts/ lives at <repo>/backend/scripts on the host and at /app/scripts in
# the backend container. Try both so the same test file runs in either context.
_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parents[2] / "backend" / "scripts", _HERE.parents[1] / "scripts"):
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Materialize a tiny DOTA-shaped tree.

    Layout:
      tmp_path/dota_src/labels.json   (3 rows, 1 annotation each)
      tmp_path/dota_src/chips/P0003.png  (64x64 RGB)
      tmp_path/dota_src/chips/P0004.png
      tmp_path/dota_src/chips/P0007.png
    """
    src = tmp_path / "dota_src"
    chips = src / "chips"
    chips.mkdir(parents=True)
    rows = []
    for stem, cls in [("P0003", "plane"), ("P0004", "ship"), ("P0007", "plane")]:
        img = Image.new("RGB", (64, 64), color=(stem.__hash__() & 0xFF, 0, 0))
        img.save(chips / f"{stem}.png")
        rows.append({
            "chip_file": f"chips/{stem}.png",
            "modality": "rgb",
            "source": "synthetic",
            "annotations": [{"label": cls, "bbox_xyxy": [10, 10, 50, 50]}],
        })
    labels = src / "labels.json"
    labels.write_text(json.dumps(rows))
    out_root = tmp_path / "reference-chips" / "dota"
    return labels, src, out_root


def test_stage_dota_chips_succeeds_with_correct_paths(tmp_path: Path):
    from stage_dota_chips import stage

    labels, src, out_root = _write_fixture(tmp_path)
    counts = stage(labels, src, out_root)

    assert counts == {"plane": 2, "ship": 1}
    assert (out_root / "plane" / "P0003__plane.png").is_file()
    assert (out_root / "plane" / "P0007__plane.png").is_file()
    assert (out_root / "ship" / "P0004__ship.png").is_file()


def test_stage_dota_chips_fails_loudly_on_wrong_chips_dir(tmp_path: Path):
    """Passing the chips/ subdir instead of its parent causes the chip_file
    prefix to double — `chips/chips/P0003.png` doesn't exist. The script
    must NOT silently return {} in that case."""
    from stage_dota_chips import stage

    labels, src, out_root = _write_fixture(tmp_path)
    wrong_chips_dir = src / "chips"  # mistake: should be `src`

    with pytest.raises(RuntimeError, match="staged 0 chips from"):
        stage(labels, wrong_chips_dir, out_root)


def test_stage_dota_chips_main_propagates_runtime_error(tmp_path: Path):
    """_main() must propagate the RuntimeError so the process exits non-zero."""
    from stage_dota_chips import _main

    labels, src, out_root = _write_fixture(tmp_path)
    wrong_chips_dir = src / "chips"

    with pytest.raises(RuntimeError):
        _main([
            "--labels", str(labels),
            "--chips-dir", str(wrong_chips_dir),
            "--out-root", str(out_root),
        ])


def test_stage_dota_chips_empty_labels_does_not_raise(tmp_path: Path):
    """If labels.json has zero annotated rows there's nothing to stage and
    nothing to complain about — must NOT raise."""
    from stage_dota_chips import stage

    labels = tmp_path / "empty.json"
    labels.write_text(json.dumps([]))
    counts = stage(labels, tmp_path, tmp_path / "out")
    assert counts == {}
