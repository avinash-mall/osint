"""
tests/test_compare_driver.py
============================
Smoke tests for the compare_inference_layers comparison driver.

These tests use --dry-run mode so no live inference service is required.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure scripts/ is importable regardless of pytest invocation directory
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from compare_inference_layers import main  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_main(argv: list[str]) -> int:
    """Call main() with the given argv list and return its exit code."""
    return main(argv)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dry_run_produces_markdown(tmp_path: Path) -> None:
    """Run the driver with --dry-run and verify the Markdown report is written."""
    report_path = tmp_path / "report.md"

    exit_code = _run_main(
        [
            "--dry-run",
            "--slice", "dota",
            "--max-chips", "3",
            "--output", str(report_path),
        ]
    )

    assert exit_code == 0, f"main() returned non-zero exit code: {exit_code}"
    assert report_path.exists(), f"Report file was not created at {report_path}"

    content = report_path.read_text(encoding="utf-8")
    assert "## Box Detectors" in content, (
        "'## Box Detectors' section missing from report.\n"
        f"Report content:\n{content[:500]}"
    )


def test_dry_run_produces_json_artifact(tmp_path: Path) -> None:
    """--json-output produces a valid JSON file containing 'results'."""
    import json

    report_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"

    exit_code = _run_main(
        [
            "--dry-run",
            "--slice", "dota",
            "--max-chips", "3",
            "--output", str(report_path),
            "--json-output", str(json_path),
        ]
    )

    assert exit_code == 0
    assert json_path.exists(), f"JSON artifact was not created at {json_path}"

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "results" in data, "'results' key missing from JSON artifact"
    assert isinstance(data["results"], list)


def test_dry_run_all_configs_present(tmp_path: Path) -> None:
    """All 6 layer configurations appear in the JSON artifact."""
    import json

    report_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"

    _run_main(
        [
            "--dry-run",
            "--slice", "dota",
            "--max-chips", "3",
            "--output", str(report_path),
            "--json-output", str(json_path),
        ]
    )

    data = json.loads(json_path.read_text(encoding="utf-8"))
    config_names = [r["config_name"] for r in data["results"]]
    expected = [
        "sam3_only",
        "sam3+dota_obb",
        "sam3+grounding_dino",
        "sam3+dota_obb+grounding_dino",
    ]
    for name in expected:
        assert name in config_names, f"Config '{name}' missing from results"


def test_dry_run_markdown_has_baseline_row(tmp_path: Path) -> None:
    """The Markdown table contains the sam3 (baseline) row."""
    report_path = tmp_path / "report.md"

    _run_main(
        [
            "--dry-run",
            "--slice", "dota",
            "--max-chips", "3",
            "--output", str(report_path),
        ]
    )

    content = report_path.read_text(encoding="utf-8")
    assert "sam3 (baseline)" in content, (
        "Baseline row 'sam3 (baseline)' not found in report.\n"
        f"Report:\n{content[:800]}"
    )


def test_dry_run_via_subprocess(tmp_path: Path) -> None:
    """Ensure the script is runnable as __main__ via subprocess."""
    script = _SCRIPTS_DIR / "compare_inference_layers.py"
    report_path = tmp_path / "subprocess_report.md"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dry-run",
            "--slice", "dota",
            "--max-chips", "3",
            "--output", str(report_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"Script exited with code {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "## Box Detectors" in content


def test_dry_run_segmenter_slice_produces_section(tmp_path: Path) -> None:
    """--dry-run --slice hls_burn produces report with ## Semantic Segmenters section."""
    report_path = tmp_path / "segmenter_report.md"

    exit_code = _run_main(
        [
            "--dry-run",
            "--slice", "hls_burn",
            "--max-chips", "4",
            "--output", str(report_path),
        ]
    )

    assert exit_code == 0, f"main() returned non-zero exit code: {exit_code}"
    assert report_path.exists(), f"Report file was not created at {report_path}"

    content = report_path.read_text(encoding="utf-8")
    assert "## Semantic Segmenters" in content, (
        "'## Semantic Segmenters' section missing from report.\n"
        f"Report content:\n{content[:1000]}"
    )


def test_dry_run_sen1floods_segmenter_section(tmp_path: Path) -> None:
    """--dry-run --slice sen1floods also produces ## Semantic Segmenters section."""
    report_path = tmp_path / "floods_report.md"

    exit_code = _run_main(
        [
            "--dry-run",
            "--slice", "sen1floods",
            "--max-chips", "4",
            "--output", str(report_path),
        ]
    )

    assert exit_code == 0, f"main() returned non-zero exit code: {exit_code}"
    content = report_path.read_text(encoding="utf-8")
    assert "## Semantic Segmenters" in content, (
        "'## Semantic Segmenters' section missing from report.\n"
        f"Report content:\n{content[:1000]}"
    )


def test_dry_run_hls_burn_json_has_segmenter_results(tmp_path: Path) -> None:
    """--slice hls_burn JSON artifact has 'segmenter_results' key."""
    report_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"

    exit_code = _run_main(
        [
            "--dry-run",
            "--slice", "hls_burn",
            "--max-chips", "4",
            "--output", str(report_path),
            "--json-output", str(json_path),
        ]
    )

    assert exit_code == 0
    assert json_path.exists()

    import json as _json
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert "segmenter_results" in data, "'segmenter_results' key missing from JSON"
    assert isinstance(data["segmenter_results"], list)
    config_names = [r["config_name"] for r in data["segmenter_results"]]
    assert "sam3_only" in config_names
    assert "sam3+prithvi" in config_names


def test_dry_run_embedding_slice_produces_section(tmp_path: Path) -> None:
    """--dry-run --slice embedding produces ## Embedding Models section."""
    import json as _json

    report_path = tmp_path / "embedding_report.md"
    json_path = tmp_path / "embedding_report.json"

    exit_code = _run_main(
        [
            "--dry-run",
            "--slice", "embedding",
            "--max-chips", "3",
            "--output", str(report_path),
            "--json-output", str(json_path),
        ]
    )

    assert exit_code == 0, f"main() returned non-zero exit code: {exit_code}"
    assert report_path.exists(), f"Report file was not created at {report_path}"

    content = report_path.read_text(encoding="utf-8")
    assert "## Embedding Models" in content, (
        "'## Embedding Models' section missing from report.\n"
        f"Report content:\n{content[:1000]}"
    )

    # All current embedding config names should appear in the report
    expected_configs = ["sam3_only", "sam3+dinov3_sat", "sam3+terramind"]
    for config_name in expected_configs:
        assert config_name in content, (
            f"Config '{config_name}' missing from Embedding Models table.\n"
            f"Report content:\n{content[:1500]}"
        )

    # JSON artifact should have embedding_results key with the same configs
    assert json_path.exists(), f"JSON artifact was not created at {json_path}"
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert "embedding_results" in data, "'embedding_results' key missing from JSON artifact"
    assert isinstance(data["embedding_results"], list)
    emb_config_names = [r["config_name"] for r in data["embedding_results"]]
    for config_name in expected_configs:
        assert config_name in emb_config_names, (
            f"Config '{config_name}' missing from embedding_results JSON"
        )


def test_dry_run_all_slice_produces_full_report(tmp_path: Path) -> None:
    """--dry-run --slice all produces all four report sections."""
    report_path = tmp_path / "all_report.md"
    json_path = tmp_path / "all_report.json"

    exit_code = _run_main(
        [
            "--dry-run",
            "--slice", "all",
            "--max-chips", "3",
            "--output", str(report_path),
            "--json-output", str(json_path),
        ]
    )

    assert exit_code == 0, f"main() returned non-zero exit code: {exit_code}"
    assert report_path.exists(), f"Report file was not created at {report_path}"

    content = report_path.read_text(encoding="utf-8")

    assert "## Box Detectors" in content, (
        "'## Box Detectors' section missing from report.\n"
        f"Report content:\n{content[:500]}"
    )
    assert "## Semantic Segmenters" in content, (
        "'## Semantic Segmenters' section missing from report.\n"
        f"Report content:\n{content[:1000]}"
    )
    assert "## Embedding Models" in content, (
        "'## Embedding Models' section missing from report.\n"
        f"Report content:\n{content[:1000]}"
    )
    assert "## Cumulative Pipeline" in content, (
        "'## Cumulative Pipeline' section missing from report.\n"
        f"Report content:\n{content[:1500]}"
    )
    assert "## Recommendations" in content, (
        "'## Recommendations' section missing from report.\n"
        f"Report content:\n{content[:2000]}"
    )

    # JSON should have results, segmenter_results, embedding_results
    import json as _json
    assert json_path.exists(), f"JSON artifact was not created at {json_path}"
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert "results" in data
    assert "segmenter_results" in data
    assert "embedding_results" in data
    assert data["slice"] == "all"


def test_dry_run_triage_set(tmp_path: Path) -> None:
    """--triage-set wires through to iter_triage and produces a Box Detectors report."""
    import struct
    import zlib

    import yaml

    # Build a minimal on-disk triage set
    triage_dir = tmp_path / "triage"
    chips_dir = triage_dir / "chips"
    chips_dir.mkdir(parents=True)

    def _png(w: int = 32, h: int = 32) -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            head = struct.pack(">I", len(data)) + tag + data
            return head + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        raw = b""
        for _ in range(h):
            raw += b"\x00" + bytes([180] * w * 3)
        compressed = zlib.compress(raw)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", compressed)
            + chunk(b"IEND", b"")
        )

    (chips_dir / "u1_0.png").write_bytes(_png())
    (chips_dir / "u1_0.json").write_text(
        '{"modality":"rgb","sensor":"optical","width":32,"height":32}'
    )
    (triage_dir / "annotations.yaml").write_text(
        yaml.safe_dump(
            {"chips": [{"chip": "u1_0.png", "sensor": "optical",
                        "expected_labels": ["aircraft"]}]}
        )
    )

    report_path = tmp_path / "triage_report.md"
    json_path = tmp_path / "triage_report.json"

    exit_code = _run_main(
        [
            "--dry-run",
            "--triage-set", str(triage_dir),
            "--max-chips", "5",
            "--output", str(report_path),
            "--json-output", str(json_path),
        ]
    )

    assert exit_code == 0
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "## Box Detectors" in content

    import json as _json
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    assert data["slice"] == "triage"
    assert "results" in data
    assert len(data["results"]) > 0
