"""Unit tests for scripts/fetch_reference_datasets.py.

Avoids the network — the manifest-driven adapter is tested with a fake
http_open, and the HF adapter is skipped if huggingface_hub isn't reachable.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Manifest-driven adapter
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, items: list[dict]) -> Path:
    """Write a minimal Wikimedia-shaped manifest."""
    manifest = {
        "source_dataset": "wikimedia",
        "default_license_spdx": "CC-BY-SA-4.0",
        "platforms": [
            {
                "platform_name": "Test Platform",
                "view_domain": "ground",
                "items": items,
            },
        ],
    }
    path = tmp_path / "wikimedia.json"
    path.write_text(json.dumps(manifest))
    return path


def _fake_url_open(payload: bytes):
    """Return a context-manager mock for urllib.request.urlopen."""
    from unittest.mock import MagicMock
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda self_: self_
    mock_resp.__exit__ = lambda self_, *a: False
    return mock_resp


def test_manifest_fetcher_downloads_and_writes_provenance(tmp_path: Path, monkeypatch):
    from fetch_reference_datasets import _fetch_from_manifest

    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200  # plausible PNG-ish bytes
    expected_sha = hashlib.sha256(payload).hexdigest()

    manifest_path = _make_manifest(tmp_path, [
        {
            "url": "https://example.test/image.jpg",
            "sha256": expected_sha,
            "license_spdx": "PD-USGov",
            "attribution": "Test Author",
        },
    ])

    import urllib.request as _urlreq
    monkeypatch.setattr(_urlreq, "urlopen", lambda *a, **kw: _fake_url_open(payload))

    out = tmp_path / "out"
    r = _fetch_from_manifest(out, manifest_path)

    assert r.status == "ok", r.detail
    assert r.chips_written == 1
    assert (out / "wikimedia" / "MANIFEST.json").is_file()
    written_manifest = json.loads((out / "wikimedia" / "MANIFEST.json").read_text())
    assert written_manifest["chip_count"] == 1
    entry = written_manifest["chips"][0]
    assert entry["sha256"] == expected_sha
    assert entry["license_spdx"] == "PD-USGov"
    assert entry["attribution"] == "Test Author"
    assert entry["source_url"] == "https://example.test/image.jpg"


def test_manifest_fetcher_is_idempotent(tmp_path: Path, monkeypatch):
    """Re-running with the same manifest body short-circuits via marker."""
    from fetch_reference_datasets import _fetch_from_manifest

    payload = b"binary-data-here" * 32
    expected_sha = hashlib.sha256(payload).hexdigest()
    manifest_path = _make_manifest(tmp_path, [
        {"url": "https://example.test/x.jpg", "sha256": expected_sha, "license_spdx": "PD-USGov"},
    ])

    import urllib.request as _urlreq
    call_counter = {"n": 0}

    def _counting_open(*a, **kw):
        call_counter["n"] += 1
        return _fake_url_open(payload)

    monkeypatch.setattr(_urlreq, "urlopen", _counting_open)

    out = tmp_path / "out"
    r1 = _fetch_from_manifest(out, manifest_path)
    assert r1.status == "ok"
    n_after_first = call_counter["n"]

    # Second call with no manifest changes → marker present → no downloads.
    r2 = _fetch_from_manifest(out, manifest_path)
    assert r2.status == "skipped"
    assert call_counter["n"] == n_after_first


def test_manifest_fetcher_handles_missing_manifest(tmp_path: Path):
    from fetch_reference_datasets import _fetch_from_manifest

    r = _fetch_from_manifest(tmp_path / "out", tmp_path / "no-such.json")
    assert r.status == "skipped"
    assert "missing" in r.detail


# ---------------------------------------------------------------------------
# Drop-in adapter
# ---------------------------------------------------------------------------


def test_dropin_adapter_skips_when_dir_absent(tmp_path: Path):
    from fetch_reference_datasets import _fetch_dropin_only

    out = tmp_path / "out"
    dropin = tmp_path / "drops"  # never created
    r = _fetch_dropin_only("xview", out, dropin)
    assert r.status == "skipped"
    assert "no drop-in tree" in r.detail


def test_dropin_adapter_handles_per_class_layout(tmp_path: Path):
    from PIL import Image

    from fetch_reference_datasets import _fetch_dropin_only

    dropin = tmp_path / "drops"
    plane_dir = dropin / "xview" / "F-16"
    plane_dir.mkdir(parents=True)
    img = Image.new("RGB", (32, 32), color=(200, 50, 50))
    img.save(plane_dir / "f16_001.png")

    out = tmp_path / "out"
    r = _fetch_dropin_only("xview", out, dropin)

    assert r.status == "ok", r.detail
    assert r.chips_written == 1
    assert (out / "xview" / "F-16" / "f16_001.png").is_file()
    manifest = json.loads((out / "xview" / "MANIFEST.json").read_text())
    assert manifest["chips"][0]["class_name"] == "F-16"


# ---------------------------------------------------------------------------
# Top-level run() with no adapters succeeding
# ---------------------------------------------------------------------------


def test_run_emits_summary_digest(tmp_path: Path, monkeypatch):
    """When all adapters skip, run() still writes MANIFEST.sha256."""
    from fetch_reference_datasets import run

    # Force every HF-bound adapter to skip by patching the credential read.
    import fetch_reference_datasets as mod
    monkeypatch.setattr(mod, "_load_hf_token", lambda: None)

    out = tmp_path / "out"
    results = run(
        out=out,
        dropin_root=tmp_path / "drops",  # empty
        manifests_root=tmp_path / "manifests",  # empty
        max_chips_per_class=10,
        only={"dota_v2", "rareplanes_synth", "fair1m"},  # all skip without token
    )
    assert all(r.status == "skipped" for r in results), [
        (r.dataset, r.status, r.detail) for r in results
    ]
    assert (out / "MANIFEST.sha256").is_file()
