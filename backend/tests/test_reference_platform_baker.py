"""Integration tests for backend/scripts/bake_reference_index.py and helpers.

The DOTA pipeline is exercised against a small synthetic chip set; the
inference-sam3 /embed HTTP call is mocked so the test is self-contained.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _cleanup_pytest_rows():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM reference_chips
             WHERE source_dataset = 'pytest-bake-dota'
        """)
        cur.execute("""
            DELETE FROM reference_platforms
             WHERE platform_name LIKE 'pytest-bake-%'
        """)


@pytest.fixture(scope="module")
def ensured_schema():
    from platform_schema import ensure_reference_platform_tables
    ensure_reference_platform_tables()
    _cleanup_pytest_rows()
    yield
    _cleanup_pytest_rows()


def test_upsert_reference_platform_is_idempotent(ensured_schema):
    from database import postgis_db
    from reference_platform_db import upsert_reference_platform
    with postgis_db.get_cursor(commit=True) as cur:
        a = upsert_reference_platform(
            cur,
            platform_name="pytest-bake-A",
            platform_family="Fighter Aircraft",
            role="initial role",
        )
        b = upsert_reference_platform(
            cur,
            platform_name="pytest-bake-A",
            platform_family="Fighter Aircraft",
            role="updated role",
        )
    assert a == b, "same platform_name must return same id"

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("SELECT role FROM reference_platforms WHERE id = %s", (a,))
        row = cur.fetchone()
    assert row["role"] == "updated role"


def test_insert_reference_chip_roundtrips_embedding(ensured_schema):
    from database import postgis_db
    from reference_platform_db import insert_reference_chip, upsert_reference_platform

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-bake-B", platform_family="ShipCategory"
        )
        v = [0.1] * 1024
        chip_id = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-bake-dota",
            chip_path="/tmp/pytest-bake-chip-1.png",
            embedding=v,
            license_spdx="CC-BY-4.0",
        )
    assert chip_id is not None

    # Second insert with same (platform_id, chip_path) must upsert, not duplicate
    with postgis_db.get_cursor(commit=True) as cur:
        chip_id_2 = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-bake-dota",
            chip_path="/tmp/pytest-bake-chip-1.png",
            embedding=[0.2] * 1024,
            license_spdx="CC-BY-4.0",
        )
    assert chip_id_2 == chip_id, "second insert on same (platform_id, chip_path) must upsert"


def test_insert_reference_chip_rejects_wrong_dimension(ensured_schema):
    from database import postgis_db
    from reference_platform_db import insert_reference_chip, upsert_reference_platform

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-bake-dim", platform_family="DimCategory"
        )
        with pytest.raises(ValueError, match="must be 1024-d"):
            insert_reference_chip(
                cur,
                platform_id=pid,
                view_domain="overhead",
                source_dataset="pytest-bake-dota",
                chip_path="/tmp/pytest-bake-dim.png",
                embedding=[0.0] * 512,  # wrong dim for overhead
                license_spdx="CC-BY-4.0",
            )


def test_recompute_platform_centroids_averages_chips(ensured_schema):
    from database import postgis_db
    from reference_platform_db import (
        insert_reference_chip,
        recompute_platform_centroids,
        upsert_reference_platform,
    )

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-bake-C", platform_family="CentroidCategory"
        )
        for i, val in enumerate([0.1, 0.2, 0.3]):
            insert_reference_chip(
                cur,
                platform_id=pid,
                view_domain="overhead",
                source_dataset="pytest-bake-dota",
                chip_path=f"/tmp/pytest-bake-C-{i}.png",
                embedding=[val] * 1024,
                license_spdx="CC-BY-4.0",
            )
        n = recompute_platform_centroids(cur, platform_id=pid)
    assert n == 1, f"expected 1 platform updated, got {n}"

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT centroid_overhead, view_domains FROM reference_platforms WHERE id = %s",
            (pid,),
        )
        row = cur.fetchone()
    # AVG of [0.1, 0.2, 0.3] is 0.2 per dim
    centroid = row["centroid_overhead"]
    assert centroid is not None
    arr = np.asarray(centroid, dtype=np.float32)
    assert arr.shape == (1024,)
    assert pytest.approx(float(arr.mean()), abs=1e-5) == 0.2
    assert "overhead" in row["view_domains"]


def test_bake_script_seeds_dota_platforms_and_inserts_chips(ensured_schema, tmp_path, monkeypatch):
    """End-to-end small-fixture run of the bake script with a mocked /embed.

    The script must:
      1. Read the seed manifest.
      2. Upsert each manifest platform.
      3. For each chip file under the fixture root, POST to /embed (mocked).
      4. Insert each chip with the returned embedding.
      5. Recompute centroids.
    """
    import io, base64, struct
    from PIL import Image

    # Build a 2-platform, 3-chip-each fixture seed
    fixture_seed = {
        "version": "pytest-fixture",
        "platforms": [
            {"platform_name": "pytest-bake-plane",
             "platform_family": "Plane",
             "source_terms_per_dataset": {"dota": ["plane"]}},
            {"platform_name": "pytest-bake-ship",
             "platform_family": "Ship",
             "source_terms_per_dataset": {"dota": ["ship"]}},
        ],
    }
    seed_path = tmp_path / "reference_platforms.seed.json"
    seed_path.write_text(json.dumps(fixture_seed))

    # Build a fixture chip tree with 3 small PNGs per class
    chips_root = tmp_path / "chips"
    for cls in ("plane", "ship"):
        (chips_root / cls).mkdir(parents=True)
        for i in range(3):
            img = Image.new("RGB", (32, 32), color=(i * 10, 50, 80))
            img.save(chips_root / cls / f"chip_{i}.png")

    # Mock the /embed HTTP call to return a deterministic fp16-encoded vector
    def _fake_post(url, files, timeout):
        cls_name = Path(files["image"][0]).parent.name
        # Vector that differs per class; identical across chips of the same class
        v = np.full(1024, 0.1 if cls_name == "plane" else 0.5, dtype=np.float16)
        b64 = base64.b64encode(v.tobytes()).decode("ascii")
        class _R:
            status_code = 200
            def json(self):
                return {"model": "facebook/dinov3-vitl16-pretrain-sat493m",
                        "dim": 1024, "fp16_b64": b64}
        return _R()

    from scripts import bake_reference_index as bake
    monkeypatch.setattr(bake, "_post_embed", _fake_post)

    rows_written = bake.run(
        seed_path=str(seed_path),
        dataset="dota",
        dataset_root=str(chips_root),
        license_spdx="CC-BY-4.0",
        max_chips_per_class=10,
    )
    assert rows_written["platforms"] == 2
    assert rows_written["chips"] == 6
    assert rows_written["centroids"] == 2

    # Confirm centroids are non-null and differ between platforms
    with postgis_db_cursor() as cur:
        cur.execute(
            "SELECT platform_name, centroid_overhead FROM reference_platforms "
            "WHERE platform_name IN ('pytest-bake-plane', 'pytest-bake-ship')"
        )
        rows = {r["platform_name"]: np.asarray(r["centroid_overhead"], dtype=np.float32)
                for r in cur.fetchall()}
    assert rows["pytest-bake-plane"].mean() == pytest.approx(0.1, abs=5e-4)
    assert rows["pytest-bake-ship"].mean() == pytest.approx(0.5, abs=5e-4)


def postgis_db_cursor():
    """Small adapter so the test above can use a plain `with … as cur:` block."""
    from database import postgis_db
    return postgis_db.get_cursor(commit=False)
