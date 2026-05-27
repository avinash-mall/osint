"""Integration tests for GET /api/reference-chips/{chip_id}/image."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="module", autouse=True)
def _setup_env():
    os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-replace-1234567890abcdef")
    os.environ.setdefault("ADMIN_USERNAME", "test-admin")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import main  # noqa: WPS433
    return TestClient(main.app)


def _login(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text


def _cleanup():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-chip-image'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-chip-image-%'")


@pytest.fixture(scope="module")
def fixture_chip(tmp_path_factory):
    """Stage a real PNG file at /data/datasets/reference-chips/pytest/...
    and insert a reference_chips row pointing at it."""
    from PIL import Image
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import upsert_reference_platform, insert_reference_chip
    from database import postgis_db
    ensure_reference_platform_tables()
    _cleanup()

    # Write a synthetic PNG inside /data/datasets/ — the only path the route allows.
    chip_dir = Path("/data/datasets/reference-chips/pytest")
    chip_dir.mkdir(parents=True, exist_ok=True)
    chip_path = chip_dir / "pytest-chip-image.png"
    Image.new("RGB", (32, 32), color=(120, 50, 80)).save(chip_path)

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-chip-image-X", platform_family="PytestFam"
        )
        chip_id = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-chip-image",
            chip_path=str(chip_path),
            embedding=np.full(1024, 0.5, dtype=np.float32),
            license_spdx="CC0-1.0",
        )
    yield {"chip_id": chip_id, "chip_path": chip_path}
    _cleanup()
    try:
        chip_path.unlink()
    except OSError:
        pass


def test_chip_image_requires_auth(client, fixture_chip):
    resp = client.get(f"/api/reference-chips/{fixture_chip['chip_id']}/image")
    assert resp.status_code == 401


def test_chip_image_returns_png_for_valid_id(client, fixture_chip):
    _login(client)
    resp = client.get(f"/api/reference-chips/{fixture_chip['chip_id']}/image")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("image/png")
    assert len(resp.content) > 0


def test_chip_image_404_for_unknown_id(client):
    _login(client)
    resp = client.get(f"/api/reference-chips/{uuid.uuid4()}/image")
    assert resp.status_code == 404


def test_chip_image_403_for_path_outside_data_datasets(client, monkeypatch):
    """A chip row whose chip_path points outside /data/datasets MUST be rejected
    even if the row exists. Guards against future bad data + path traversal."""
    _login(client)
    from database import postgis_db
    from reference_platform_db import upsert_reference_platform, insert_reference_chip
    import numpy as np
    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-chip-image-evil", platform_family="EvilFam"
        )
        # Path points outside the allowed root
        chip_id = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-chip-image",
            chip_path="/etc/passwd",
            embedding=np.full(1024, 0.0, dtype=np.float32),
            license_spdx="CC0-1.0",
        )
    resp = client.get(f"/api/reference-chips/{chip_id}/image")
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"
