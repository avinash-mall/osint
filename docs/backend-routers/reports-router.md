# Reports Router (`/api/reports/*`)

**Path:** [backend/routers/reports.py](../../backend/routers/reports.py)
**Lines:** ~150
**Depends on:** [backend/terrain.py](../../backend/terrain.py), [database.postgis_db](../../backend/database.py), `reportlab`, optional `mgrs`

## Purpose

Operational PDF/JSON exports for the SelectionPanel and AI-action paths. Currently hosts the Target Package PDF endpoint; intended to grow with other operator-facing exports.

## Endpoints

| Method | Path | Source | Computes |
|---|---|---|---|
| `POST` | `/api/reports/target-package/{detection_id}` | [reports.py#L57](../../backend/routers/reports.py#L57) | One-page A4 PDF Target Package |

## Why this design

- **PDF is built server-side with ReportLab** — pure-Python, no headless browser, no fonts to ship. Adds ~3 MB to the backend image which is acceptable. Frontend just streams the response and triggers a download; no client-side ReportLab/jsPDF needed.
- **The package is built from already-persisted detection state** — no live re-inference. Re-running the export produces the same PDF for the same detection, which matters for after-action archival.
- **Elevation is sampled live from the DEM**, not stored on the detection row. Elevation is cheap and we want it to track DEM updates without a migration. Falls back to `—` when the DEM is not configured.
- **Threat / Affiliation read the keys the writers actually use** — `metadata.threat_level` / `metadata.allegiance` (PATCH `/tag`, PUT `/details`) with the `detections.threat_level` / `detections.affiliation` columns as fallback. The old `metadata.threat` / `metadata.affiliation` keys were never written by anything.
- **MGRS** uses the optional Python `mgrs` package; missing dep degrades to a lat/lon string rather than failing the export.

## Failure modes

- `404` — detection not found or soft-deleted.
- `503` — `reportlab` not installed.
- Missing DEM → `Elevation: —` line in the PDF (no error).
- Missing `mgrs` package → MGRS shows as a lat/lon string.

## Cross-references

- [backend/reports-and-collections.md](../backend/reports-and-collections.md)
- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [decisions/audit-fixes-api-layer-2026-06-11.md](../decisions/audit-fixes-api-layer-2026-06-11.md) — the 2026-06-11 API-layer audit batch
