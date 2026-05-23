"""Reports router — operational PDF/JSON exports.

Currently hosts the Target Package PDF endpoint for the SelectionPanel's
"Generate Target Package" button. The package is built entirely from already-
persisted detection state (no live re-analysis) so it remains repeatable for
post-mission archival.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from database import postgis_db
from terrain import dem_available, sample_elevation

logger = logging.getLogger(__name__)
router = APIRouter()


def _load_detection(detection_id: int) -> Optional[dict]:
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, class, confidence, metadata, source, created_at, pass_id, "
            "ST_Y(centroid) AS lat, ST_X(centroid) AS lon, "
            "ST_AsGeoJSON(geom) AS geom_json "
            "FROM detections WHERE id = %s AND deleted_at IS NULL",
            (detection_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _format_mgrs(lat: Optional[float], lon: Optional[float]) -> str:
    if lat is None or lon is None:
        return "n/a"
    try:
        # Local import — pure-python mgrs not vendored on backend; degrade to lat/lon.
        from mgrs import MGRS  # type: ignore
        return MGRS().toMGRS(lat, lon, MGRSPrecision=5)
    except Exception:
        return f"{lat:.5f}, {lon:.5f}"


@router.post("/api/reports/target-package/{detection_id}")
def export_target_package(detection_id: int):
    """Build and stream a PDF Target Package for the given detection.

    Aggregates centroid (lat/lon + MGRS), DEM elevation (when available),
    OBB size estimate (length/width/area/bearing), confidence and source
    metadata into a single PDF page.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"PDF backend unavailable: {exc}") from exc

    det = _load_detection(detection_id)
    if not det:
        raise HTTPException(status_code=404, detail="detection not found")

    metadata = det.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    size = metadata.get("size_estimate") or {}
    label = metadata.get("label") or det.get("class") or "unclassified"
    parent_class = metadata.get("parent_class") or det.get("class") or "unknown"

    lat = det.get("lat")
    lon = det.get("lon")
    elevation_m: Optional[float] = None
    if lat is not None and lon is not None and dem_available():
        try:
            elevation_m = sample_elevation(float(lat), float(lon))
        except Exception:
            elevation_m = None

    mgrs_str = _format_mgrs(lat, lon)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    captured_at = metadata.get("acquisition_time") or (
        det.get("created_at").isoformat() if det.get("created_at") else "n/a"
    )

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Header band
    pdf.setFillColorRGB(0.06, 0.13, 0.20)
    pdf.rect(0, height - 28 * mm, width, 28 * mm, stroke=0, fill=1)
    pdf.setFillColorRGB(0.92, 0.95, 0.98)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(15 * mm, height - 14 * mm, "TARGET PACKAGE")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(15 * mm, height - 20 * mm, f"DET-{detection_id}  ·  {label.upper()}  ·  {parent_class}")
    pdf.drawRightString(width - 15 * mm, height - 14 * mm, f"GENERATED {now_iso}")
    pdf.drawRightString(width - 15 * mm, height - 20 * mm, "CLASSIFICATION: UNCLASSIFIED")

    pdf.setFillColorRGB(0, 0, 0)
    y = height - 40 * mm

    def section(title: str):
        nonlocal y
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(15 * mm, y, title.upper())
        pdf.setLineWidth(0.4)
        pdf.line(15 * mm, y - 1.5 * mm, width - 15 * mm, y - 1.5 * mm)
        y -= 6 * mm

    def kv(key: str, value: str):
        nonlocal y
        pdf.setFont("Helvetica", 9)
        pdf.drawString(18 * mm, y, key)
        pdf.setFont("Courier", 9)
        pdf.drawString(60 * mm, y, value)
        y -= 5 * mm

    section("Geolocation")
    kv("WGS84", f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else "n/a")
    kv("MGRS", mgrs_str)
    kv("Elevation", f"{elevation_m:.1f} m MSL" if elevation_m is not None else "—")
    y -= 2 * mm

    section("Dimensions (OBB)")
    if size:
        length_m = size.get("length_m")
        width_m = size.get("width_m")
        area_m2 = size.get("area_m2")
        orientation_deg = size.get("orientation_deg")
        kv("Length", f"{length_m:.1f} m" if isinstance(length_m, (int, float)) else "—")
        kv("Width", f"{width_m:.1f} m" if isinstance(width_m, (int, float)) else "—")
        kv("Area", f"{area_m2:,.0f} m²" if isinstance(area_m2, (int, float)) else "—")
        kv("Bearing", f"{orientation_deg:.0f}°" if isinstance(orientation_deg, (int, float)) else "—")
    else:
        kv("Size estimate", "no OBB available")
    y -= 2 * mm

    section("Provenance")
    kv("Source", str(det.get("source") or "ai"))
    kv("Captured", str(captured_at))
    kv("Confidence", f"{float(det.get('confidence') or 0) * 100:.1f}%")
    kv("Pass ID", str(det.get("pass_id") or "manual"))
    kv("Branch", str(metadata.get("branch_id") or "n/a"))
    y -= 2 * mm

    threat = metadata.get("threat") or "unassessed"
    affil = metadata.get("affiliation") or "unknown"
    section("Classification log")
    kv("Threat", str(threat))
    kv("Affiliation", str(affil))

    pdf.setFont("Helvetica-Oblique", 8)
    pdf.setFillColorRGB(0.4, 0.46, 0.55)
    pdf.drawString(15 * mm, 12 * mm, "Sentinel GEOINT · Target Package export — review before operational use.")
    pdf.showPage()
    pdf.save()
    buf.seek(0)

    headers = {"Content-Disposition": f'attachment; filename="target-{detection_id}.pdf"'}
    return StreamingResponse(buf, media_type="application/pdf", headers=headers)
