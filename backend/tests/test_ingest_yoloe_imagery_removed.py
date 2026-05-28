from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _ensure_envs() -> None:
    os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-replace-1234567890abcdef")
    os.environ.setdefault("ADMIN_USERNAME", "test-admin")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:9999")
    os.environ.setdefault("POSTGIS_URI", "postgresql://nobody:nobody@localhost:9999/none")


def test_imagery_yolo26_is_rejected_before_staging():
    _ensure_envs()
    from routers.ingest import _resolve_upload_modes

    with pytest.raises(HTTPException) as exc:
        _resolve_upload_modes("imagery", "yolo26", "pcs")

    assert exc.value.status_code == 400
    assert "FMV-only" in str(exc.value.detail)


def test_fmv_yolo26_still_maps_to_yoloe_capable_mode():
    _ensure_envs()
    from routers.ingest import _resolve_upload_modes

    resolved_model, resolved_prompt_mode, fmv_model_choice, fmv_mode = _resolve_upload_modes(
        "fmv",
        "yolo26",
        "amg",
    )

    assert resolved_model is None
    assert resolved_prompt_mode is None
    assert fmv_model_choice == "yolo26"
    assert fmv_mode == "amg"
