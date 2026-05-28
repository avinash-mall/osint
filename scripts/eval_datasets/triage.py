"""Dataset loader for an analyst-curated triage set built from recent uploads.

Triage sets live under ``bench/triage/<date>/`` and pair each PNG chip with a
human-edited ``annotations.yaml`` row that lists the labels an analyst expects
the model to find on that chip. This is the production-image benchmark used to
verify each round of detection-quality work — see Tier 0 of the detection
quality plan.

Layout
------
::

    bench/triage/<date>/
    ├── annotations.yaml
    └── chips/
        ├── <upload-id>_<idx>.png
        └── <upload-id>_<idx>.json   # per-chip metadata sidecar

``iter_triage`` yields the same 4-tuple shape as ``iter_dota``::

    (chip_bytes, modality, prompts, ground_truth)

so the comparison driver in ``scripts/compare_inference_layers.py`` can consume
it without changes to the per-chip evaluation logic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Generator

import yaml

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def iter_triage(
    triage_dir: Path | str,
    rgb_only: bool = True,
) -> Generator[tuple[bytes, str, list[str], list[dict]], None, None]:
    """Yield (chip_bytes, modality, prompts, ground_truth) tuples from a triage set.

    Parameters
    ----------
    triage_dir:
        Path to a triage set produced by ``scripts/build_triage_set.py``. Must
        contain ``annotations.yaml`` and a ``chips/`` subdirectory.
    rgb_only:
        When True (default), only yield chips whose sidecar JSON reports
        ``modality == "rgb"``. The first round of the detection-quality plan
        is RGB-optical only; pass ``rgb_only=False`` to evaluate every chip.

    Yields
    ------
    chip_bytes : bytes
        Raw PNG bytes of the chip.
    modality : str
        Modality string from the chip's sidecar JSON; defaults to ``"rgb"`` if
        the sidecar is missing.
    prompts : list[str]
        ``expected_labels`` from ``annotations.yaml`` for this chip. The
        comparison driver feeds these to the inference service as
        ``text_prompts``. May be empty for chips where the analyst expects no
        detections (used to measure false-positive rate).
    ground_truth : list[dict]
        One row per expected label, shaped
        ``{"label": str, "bbox_xyxy": [0, 0, W, H]}``. The box covers the
        whole chip because triage annotations are chip-presence labels (no
        per-object boxes). This makes per-class precision / recall scoreable
        without per-object annotation work.

    Raises
    ------
    FileNotFoundError
        ``annotations.yaml`` does not exist in ``triage_dir`` — points the
        user at ``scripts/build_triage_set.py``.
    FileNotFoundError
        A chip listed in ``annotations.yaml`` has no PNG file on disk.
    """
    triage_path = Path(triage_dir).resolve()
    yaml_path = triage_path / "annotations.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(
            f"annotations.yaml not found at {yaml_path!s}. "
            "Build the triage set first with "
            "`python scripts/build_triage_set.py --out <dir>` and then "
            "fill in expected_labels per chip."
        )

    with yaml_path.open() as fh:
        document = yaml.safe_load(fh) or {}

    chips_dir = triage_path / "chips"
    rows = document.get("chips") or []

    for row in rows:
        chip_name = row.get("chip")
        if not chip_name:
            continue

        chip_path = chips_dir / chip_name
        if not chip_path.exists():
            raise FileNotFoundError(
                f"Chip '{chip_name}' is listed in annotations.yaml but the "
                f"PNG file does not exist at {chip_path!s}."
            )

        meta = _load_sidecar(chip_path)
        modality = str(meta.get("modality", "rgb")).lower()

        if rgb_only and modality != "rgb":
            continue

        chip_bytes = chip_path.read_bytes()
        width = int(meta.get("width", 0)) or _png_width(chip_bytes)
        height = int(meta.get("height", 0)) or _png_height(chip_bytes)

        expected: list[str] = [str(lbl) for lbl in (row.get("expected_labels") or [])]
        prompts = sorted(set(expected))
        ground_truth: list[dict] = [
            {"label": lbl, "bbox_xyxy": [0, 0, width, height]}
            for lbl in expected
        ]

        yield chip_bytes, modality, prompts, ground_truth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_sidecar(chip_path: Path) -> dict:
    """Return the JSON sidecar for ``chip_path`` if it exists, else ``{}``."""
    sidecar = chip_path.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        with sidecar.open() as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _png_width(png_bytes: bytes) -> int:
    """Best-effort PNG width parser (IHDR is always the first chunk)."""
    # IHDR starts at byte 16 (8 signature + 4 length + 4 type)
    if len(png_bytes) < 24 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return 0
    return int.from_bytes(png_bytes[16:20], "big")


def _png_height(png_bytes: bytes) -> int:
    if len(png_bytes) < 28 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return 0
    return int.from_bytes(png_bytes[20:24], "big")
