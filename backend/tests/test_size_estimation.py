"""Tests for size_estimation.estimate_size — see plan
/home/avinash/.claude/plans/size-estimation-of-objects-reflective-hoare.md
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pyproj
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from size_estimation import estimate_size  # noqa: E402


UTM_33N = pyproj.CRS.from_epsg(32633)
WGS84 = pyproj.CRS.from_epsg(4326)


def _rotate(x: float, y: float, theta_rad: float) -> tuple[float, float]:
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)
    return x * cos_t - y * sin_t, x * sin_t + y * cos_t


def _rect_polygon(cx: float, cy: float, length: float, width: float, theta_rad: float) -> list[float]:
    """Return a length×width rectangle centered at (cx, cy), rotated by theta_rad (math convention)."""
    half_l = length / 2.0
    half_w = width / 2.0
    corners = [(half_l, half_w), (half_l, -half_w), (-half_l, -half_w), (-half_l, half_w)]
    out: list[float] = []
    for x, y in corners:
        rx, ry = _rotate(x, y, theta_rad)
        out.extend([cx + rx, cy + ry])
    return out


def test_projected_utm_axis_aligned_rectangle():
    """100m x 40m axis-aligned rectangle in UTM 33N."""
    poly = _rect_polygon(500_000, 5_000_000, length=100, width=40, theta_rad=0.0)
    result = estimate_size(
        geo_polygon=poly,
        crs=UTM_33N,
        pixel_width_m=1.0,
        pixel_height_m=1.0,
        mask_area_px=4000,
    )
    assert result["length_m"] == pytest.approx(100.0, abs=0.5)
    assert result["width_m"] == pytest.approx(40.0, abs=0.5)
    assert result["area_m2"] == pytest.approx(4000.0, abs=20.0)
    # Axis-aligned long axis is east-west → bearing 90° (or 270°). Accept either.
    bearing = result["orientation_deg"] % 180.0
    assert bearing == pytest.approx(90.0, abs=1.0)


def test_projected_utm_rotated_45deg():
    """50m x 20m rectangle rotated 45° in UTM."""
    poly = _rect_polygon(500_000, 5_000_000, length=50, width=20, theta_rad=math.radians(45))
    result = estimate_size(
        geo_polygon=poly,
        crs=UTM_33N,
        pixel_width_m=1.0,
        pixel_height_m=1.0,
        mask_area_px=1000,
    )
    assert result["length_m"] == pytest.approx(50.0, abs=0.5)
    assert result["width_m"] == pytest.approx(20.0, abs=0.5)
    assert result["area_m2"] == pytest.approx(1000.0, abs=20.0)
    # 45° math (CCW from east) → 45° bearing (CW from north). Long axis is symmetric so 45 or 225.
    bearing = result["orientation_deg"] % 180.0
    assert bearing == pytest.approx(45.0, abs=1.5)


def test_geographic_wgs84_equator_ship():
    """120 m × 12 m ship-like polygon at equator, defined in degrees."""
    # 1 deg lon at equator ≈ 111320 m, same for lat
    length_deg = 120.0 / 111_320.0
    width_deg = 12.0 / 111_320.0
    poly = _rect_polygon(0.0, 0.0, length=length_deg, width=width_deg, theta_rad=0.0)
    result = estimate_size(
        geo_polygon=poly,
        crs=WGS84,
        pixel_width_m=0.5,
        pixel_height_m=0.5,
        mask_area_px=0,  # force polygon fallback for area
    )
    assert result["length_m"] == pytest.approx(120.0, abs=1.0)
    assert result["width_m"] == pytest.approx(12.0, abs=1.0)
    assert result["area_m2"] == pytest.approx(120.0 * 12.0, rel=0.05)


def test_geographic_wgs84_high_latitude_square():
    """A true 100×100 m square at lat 60°N — cos(lat) projection must equalize sides."""
    lat0 = 60.0
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    length_lon_deg = 100.0 / meters_per_deg_lon
    width_lat_deg = 100.0 / meters_per_deg_lat
    cx = 10.0
    cy = lat0
    poly = [
        cx, cy,
        cx + length_lon_deg, cy,
        cx + length_lon_deg, cy + width_lat_deg,
        cx, cy + width_lat_deg,
    ]
    result = estimate_size(
        geo_polygon=poly,
        crs=WGS84,
        pixel_width_m=1.0,
        pixel_height_m=1.0,
        mask_area_px=0,
    )
    assert result["length_m"] == pytest.approx(100.0, abs=2.0)
    assert result["width_m"] == pytest.approx(100.0, abs=2.0)
    # After local-UTM projection, sides should match within 2%.
    assert abs(result["length_m"] - result["width_m"]) < 2.0


def test_uncertainty_scales_with_gsd():
    """Higher GSD → larger ±uncertainty in meters."""
    poly = _rect_polygon(500_000, 5_000_000, length=50, width=20, theta_rad=0.0)
    fine = estimate_size(
        geo_polygon=poly, crs=UTM_33N,
        pixel_width_m=0.5, pixel_height_m=0.5, mask_area_px=1000,
    )
    coarse = estimate_size(
        geo_polygon=poly, crs=UTM_33N,
        pixel_width_m=2.0, pixel_height_m=2.0, mask_area_px=1000,
    )
    assert coarse["uncertainty"]["length_m"] == pytest.approx(4.0 * fine["uncertainty"]["length_m"], rel=0.01)
    assert coarse["uncertainty"]["width_m"] == pytest.approx(4.0 * fine["uncertainty"]["width_m"], rel=0.01)


@pytest.mark.parametrize("math_angle_deg,expected_bearing_mod180", [
    (0.0, 90.0),     # long axis E-W → bearing 90°
    (45.0, 45.0),    # rotated CCW 45° → bearing 45°
    (90.0, 0.0),     # long axis N-S → bearing 0° (or 180°)
    (135.0, 135.0),  # rotated CCW 135° → bearing 135°
])
def test_orientation_bearing_from_north(math_angle_deg, expected_bearing_mod180):
    poly = _rect_polygon(500_000, 5_000_000, length=80, width=20, theta_rad=math.radians(math_angle_deg))
    result = estimate_size(
        geo_polygon=poly, crs=UTM_33N,
        pixel_width_m=1.0, pixel_height_m=1.0, mask_area_px=1600,
    )
    bearing = result["orientation_deg"] % 180.0
    # 0° and 180° are equivalent for an undirected axis
    distance = min(abs(bearing - expected_bearing_mod180), abs(bearing - expected_bearing_mod180 + 180), abs(bearing - expected_bearing_mod180 - 180))
    assert distance < 1.5, f"bearing {bearing}° not within 1.5° of {expected_bearing_mod180}°"


def test_zero_mask_falls_back_to_polygon_area():
    """When mask_area_px=0, area_m2 comes from polygon.area instead of crashing."""
    poly = _rect_polygon(500_000, 5_000_000, length=50, width=20, theta_rad=0.0)
    result = estimate_size(
        geo_polygon=poly, crs=UTM_33N,
        pixel_width_m=1.0, pixel_height_m=1.0, mask_area_px=0,
    )
    assert result["area_m2"] == pytest.approx(1000.0, abs=20.0)


def test_degenerate_polygon_returns_none():
    """Collinear / zero-area polygon should return None so the caller's try/except can skip."""
    poly = [0.0, 0.0, 1.0, 0.0, 2.0, 0.0, 3.0, 0.0]  # collinear
    result = estimate_size(
        geo_polygon=poly, crs=UTM_33N,
        pixel_width_m=1.0, pixel_height_m=1.0, mask_area_px=0,
    )
    assert result is None


def test_result_shape_has_required_fields():
    """Output must contain all keys the worker and frontend rely on."""
    poly = _rect_polygon(500_000, 5_000_000, length=80, width=30, theta_rad=0.0)
    result = estimate_size(
        geo_polygon=poly, crs=UTM_33N,
        pixel_width_m=1.0, pixel_height_m=1.0, mask_area_px=2400,
    )
    assert set(result.keys()) >= {
        "length_m", "width_m", "area_m2", "orientation_deg", "uncertainty", "source",
    }
    assert set(result["uncertainty"].keys()) >= {"length_m", "width_m", "area_m2"}
    assert result["source"] in {"obb_projected_native", "obb_geo_polygon_utm"}
