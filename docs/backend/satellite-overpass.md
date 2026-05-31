# `backend/satellite_overpass.py` — SGP4 overpass prediction (offline)

**Path:** [backend/satellite_overpass.py](../../backend/satellite_overpass.py)
**Lines:** ~300
**Depends on:** `sgp4`, `math`/`datetime` (stdlib). No DB, no network.

## Purpose

Predicts when a satellite rises above a minimum elevation over an observer point
(an AOI centroid or explicit lat/lon) and samples its sub-satellite ground
track, from analyst-supplied TLEs. Pure computation — the maths runs unchanged
air-gapped; only TLE *freshness* depends on the operator re-importing newer
elements. Powers [backend/routers/satellites.py](../../backend/routers/satellites.py).

> **Not** [backend/tracker.py](../../backend/tracker.py). That "satellite-pass
> tracker" associates *detections* across image acquisitions (Hungarian solver);
> this module is orbital mechanics. See [tracker-satellite.md](tracker-satellite.md).

## Why this design

- **SGP4 in, closed-form frames after.** `sgp4` outputs TEME state vectors. We
  rotate TEME→ECEF with GMST (IAU-82) and use closed-form WGS84 geodetic
  conversions (Bowring) for the sub-point and topocentric elevation. Polar
  motion / nutation are ignored — sub-km error, far below what overpass-window
  planning needs, and it keeps the hot loop dependency-light (no per-step pyproj
  Transformer calls).
- **Coarse stepping, contiguous-grouping passes.** `predict_passes` walks the
  window at `step_s` and groups samples where elevation ≥ threshold into passes.
  AOS/LOS are accurate to ±`step_s` — adequate for planning, and cheap.
- **TLE epoch parsed from the element line**, not derived via sgp4, so callers
  can store/display provenance without instantiating a propagator.

## Key symbols

- `Tle` dataclass — `norad_id`, `satrec()`, `epoch()` (line-1 cols 19-32).
- `parse_tle_text` — tolerant 2-/3-line parser.
- `_gmst_rad`, `_teme_to_ecef`, `_ecef_to_geodetic`, `_geodetic_to_ecef`,
  `_elevation_deg` — frame/geodetic helpers (clean-room, public formulas).
- `predict_passes` → list of `Pass` (AOS/LOS/max-elevation/duration).
- `ground_track` → `{coordinates: [[lon,lat],...], altitudes_km: [...]}`.

## Inputs / Outputs

- **In:** a `Tle`, observer lat/lon, a UTC window, `min_elevation_deg`, `step_s`.
- **Out:** `Pass` objects (`.to_dict()` → ISO timestamps + degrees) and a
  ground-track dict of `[lon, lat]` plus per-sample altitude (km).

## Failure modes

- SGP4 error codes (decayed / garbage element set) raise `ValueError`; the
  router skips that satellite and logs, rather than fabricating a pass.
- Stale TLEs silently lose accuracy over time — provenance (`epoch`, `source`)
  is surfaced so analysts can judge freshness; there is no auto-refresh offline.

## Cross-references

- Router: [backend-routers/satellites-router.md](../backend-routers/satellites-router.md)
- Decision: [decisions/why-offline-tle-import.md](../decisions/why-offline-tle-import.md)
- Tests: [backend/tests/test_satellite_overpass.py](../../backend/tests/test_satellite_overpass.py)
