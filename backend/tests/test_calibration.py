"""Unit tests for backend/calibration.py.

Covers the two file shapes the loader has to handle:
  * **wrapped** (canonical, as shipped from `assets/static/calibration/`)::

        {
          "_README": "...",
          "_measured_at": "2026-05-28",
          "_measured_against": "...",
          "temperatures": { "sam3": 0.8, "dota_obb": 1.1, ... }
        }

  * **flat** (legacy, what early dev hosts dropped in by hand)::

        { "sam3": 0.8, "dota_obb": 1.1 }

And the bake-and-rsync invariant: the placeholder JSON checked into
`assets/static/calibration/` parses and exposes the 6 expected detector
keys at T=1.0.
"""

from __future__ import annotations

import json
from pathlib import Path

import calibration


REPO_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_PATH = REPO_ROOT / "assets" / "static" / "calibration" / "model_temperatures.json"
EXPECTED_DETECTORS = {
    "sam3",
    "dota_obb",
    "yoloe",
    "sar_cfar",
}


def _set_temperature_file(monkeypatch, tmp_path: Path, payload: dict) -> Path:
    """Drop ``payload`` at a temp path and point the loader at it."""
    path = tmp_path / "model_temperatures.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.delenv("MODEL_TEMPERATURES", raising=False)
    monkeypatch.setenv("MODEL_TEMPERATURES_FILE", str(path))
    return path


def test_load_temperatures_reads_wrapped_shape(monkeypatch, tmp_path):
    """The wrapped shape (with a `temperatures` block) is the canonical one."""
    _set_temperature_file(
        monkeypatch,
        tmp_path,
        {
            "_README": "comment",
            "_measured_at": "2026-05-28",
            "temperatures": {"sam3": 0.8, "dota_obb": 1.1},
        },
    )

    out, _meta = calibration._load_temperatures()

    assert out == {"sam3": 0.8, "dota_obb": 1.1}
    # Metadata keys must NOT leak into the lookup.
    assert "_readme" not in out
    assert "_measured_at" not in out


def test_load_temperatures_reads_flat_shape(monkeypatch, tmp_path):
    """The legacy flat shape (no wrapper) must still work for back-compat."""
    _set_temperature_file(
        monkeypatch,
        tmp_path,
        {"sam3": 0.7, "dota_obb": 1.2},
    )

    out, _meta = calibration._load_temperatures()

    assert out == {"sam3": 0.7, "dota_obb": 1.2}


def test_load_temperatures_flat_shape_ignores_underscore_metadata(monkeypatch, tmp_path):
    """In the flat case, `_README`-style metadata keys are skipped silently."""
    _set_temperature_file(
        monkeypatch,
        tmp_path,
        {"_README": "ignore me", "sam3": 0.9},
    )

    out, _meta = calibration._load_temperatures()

    assert out == {"sam3": 0.9}


def test_load_temperatures_wrapped_wins_over_flat_when_both_present(monkeypatch, tmp_path):
    """If the JSON happens to carry both a top-level detector key AND a
    `temperatures` block, the wrapper wins (it's the explicit signal)."""
    _set_temperature_file(
        monkeypatch,
        tmp_path,
        {
            "sam3": 99.0,  # would be the flat-shape value
            "temperatures": {"sam3": 0.8},
        },
    )

    out, _meta = calibration._load_temperatures()

    assert out == {"sam3": 0.8}


def test_load_temperatures_missing_file_returns_empty_map(monkeypatch, tmp_path):
    """Identity-transform fallback: no file ⇒ no temperatures ⇒ T=1.0."""
    monkeypatch.delenv("MODEL_TEMPERATURES", raising=False)
    monkeypatch.setenv("MODEL_TEMPERATURES_FILE", str(tmp_path / "does-not-exist.json"))

    out, _meta = calibration._load_temperatures()

    assert out == {}


def test_temperature_for_returns_identity_when_unloaded(monkeypatch, tmp_path):
    """Sanity: with no temperatures loaded, every model resolves to 1.0."""
    monkeypatch.delenv("MODEL_TEMPERATURES", raising=False)
    monkeypatch.setenv("MODEL_TEMPERATURES_FILE", str(tmp_path / "missing.json"))
    temps, meta = calibration._load_temperatures()
    monkeypatch.setattr(calibration, "_TEMPERATURES", temps)
    monkeypatch.setattr(calibration, "_METADATA", meta)

    assert calibration.temperature_for("sam3") == 1.0
    assert calibration.temperature_for(None) == 1.0


def test_placeholder_json_parses_and_carries_expected_detectors():
    """The file we bake into the assets image must:
      * parse cleanly,
      * use the wrapped shape with a `temperatures` block,
      * cover all 6 detectors at the identity transform T=1.0.
    """
    assert PLACEHOLDER_PATH.is_file(), f"missing: {PLACEHOLDER_PATH}"

    payload = json.loads(PLACEHOLDER_PATH.read_text(encoding="utf-8"))

    assert "temperatures" in payload, "placeholder must use wrapped shape"
    temps = payload["temperatures"]
    assert set(temps.keys()) == EXPECTED_DETECTORS, (
        f"detector keys drifted; got {sorted(temps.keys())}"
    )
    for name, value in temps.items():
        assert value == 1.0, f"placeholder {name} must default to T=1.0, got {value}"


def test_status_surfaces_wrapper_metadata(monkeypatch, tmp_path):
    """status() must expose measured_at / measured_against so the dashboard
    can show when the active calibration was measured."""
    _set_temperature_file(
        monkeypatch,
        tmp_path,
        {
            "_measured_at": "2026-05-28",
            "_measured_against": "DOTA val + triage 2026-05-28",
            "temperatures": {"sam3": 0.8},
        },
    )
    temps, meta = calibration._load_temperatures()
    monkeypatch.setattr(calibration, "_TEMPERATURES", temps)
    monkeypatch.setattr(calibration, "_METADATA", meta)

    s = calibration.status()
    assert s["model_count"] == 1
    assert s["models"] == ["sam3"]
    assert s["measured_at"] == "2026-05-28"
    assert s["measured_against"] == "DOTA val + triage 2026-05-28"
    assert s["source"]  # the file path we wrote to


def test_placeholder_manifest_digest_matches_json():
    """`MANIFEST.sha256` must hold the sha256 of `model_temperatures.json`
    (entrypoint compares this digest to decide whether to rsync onto the
    `calibration_data` volume — a drift here breaks the bake invariant)."""
    import hashlib

    manifest = PLACEHOLDER_PATH.parent / "MANIFEST.sha256"
    assert manifest.is_file(), f"missing: {manifest}"

    expected = hashlib.sha256(PLACEHOLDER_PATH.read_bytes()).hexdigest()
    recorded = manifest.read_text(encoding="utf-8").strip()

    assert recorded == expected, (
        "MANIFEST.sha256 is stale; regenerate via "
        "`sha256sum model_temperatures.json | cut -d' ' -f1 > MANIFEST.sha256`"
    )
