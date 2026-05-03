from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_backend_main_helpers():
    path = REPO_ROOT / "backend" / "main.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("_KNOWN_INFERENCE_PROVIDERS")
    end = source.index("@app.post(\"/api/ingest/upload\")", start)
    namespace: dict = {}
    exec(source[start:end], namespace)
    return namespace


def load_worker_helpers():
    path = REPO_ROOT / "backend" / "worker.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("def _provider_set")
    end = source.index("def is_official_lae_detection", start)
    namespace = {"DETECTION_POLICY": {"high_confidence_threshold": 0.55}}
    exec(source[start:end], namespace)
    return namespace


def test_parse_inference_providers_accepts_mmrotate_and_dedupes():
    helpers = load_backend_main_helpers()

    assert helpers["_parse_inference_providers"]("yolo,mmrotate,lae-dino,mmrotate") == [
        "yolo",
        "mmrotate",
        "lae-dino",
    ]


def test_parse_inference_providers_ignores_unknown_and_falls_back():
    helpers = load_backend_main_helpers()

    assert helpers["_parse_inference_providers"]("unknown") == ["yolo"]
    assert helpers["_parse_inference_providers"]("") == ["yolo"]


def test_cross_provider_detection_is_confirmed():
    helpers = load_worker_helpers()
    det = {"providers": ["yolo", "mmrotate"], "confidence": 0.2}

    helpers["apply_confirmation_policy"]([det], selected_provider_count=2)

    assert det["cross_confirmed"] is True
    assert det["confirmation_status"] == "confirmed"
    assert det["confirmation_reason"] == "cross_provider"


def test_single_provider_high_confidence_is_confirmed_in_multi_provider_run():
    helpers = load_worker_helpers()
    det = {"providers": ["mmrotate"], "confidence": 0.8}

    helpers["apply_confirmation_policy"]([det], selected_provider_count=2)

    assert det["cross_confirmed"] is False
    assert det["confirmation_status"] == "confirmed"
    assert det["confirmation_reason"] == "high_confidence"


def test_single_provider_low_confidence_stays_review_candidate_in_multi_provider_run():
    helpers = load_worker_helpers()
    det = {"providers": ["mmrotate"], "confidence": 0.3, "review_status": "high_confidence"}

    helpers["apply_confirmation_policy"]([det], selected_provider_count=2)

    assert det["cross_confirmed"] is False
    assert det["confirmation_status"] == "review_candidate"
    assert det["confirmation_reason"] == "single_provider_low_confidence"
    assert det["review_status"] == "review_candidate"


def test_unrelated_provider_detections_are_not_cross_confirmed():
    helpers = load_worker_helpers()
    left = {"providers": ["yolo"], "confidence": 0.3}
    right = {"providers": ["mmrotate"], "confidence": 0.3}

    helpers["apply_confirmation_policy"]([left, right], selected_provider_count=2)

    assert left["cross_confirmed"] is False
    assert right["cross_confirmed"] is False
    assert left["confirmation_status"] == "review_candidate"
    assert right["confirmation_status"] == "review_candidate"


def test_single_provider_run_is_unchanged():
    helpers = load_worker_helpers()
    det = {"providers": ["mmrotate"], "confidence": 0.3}

    helpers["apply_confirmation_policy"]([det], selected_provider_count=1)

    assert "confirmation_status" not in det
