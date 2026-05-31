# `backend/satellite_anomaly.py` — maneuver/decay detection + mission classification

**Path:** [backend/satellite_anomaly.py](../../backend/satellite_anomaly.py)
**Lines:** ~210
**Depends on:** `math` (stdlib) + parsed elements from
[satellite_overpass.Tle.elements()](../../backend/satellite_overpass.py). No DB, no network.

## Purpose

Two offline analyses over successive TLE epochs for the same object (R1) plus a
name-based mission tag (R2):

- **maneuver** — `detect_maneuver(prev, cur)` flags a change in period /
  inclination / eccentricity, or a RAAN shift beyond expected J2 nodal
  precession, larger than TLE fitting noise.
- **decay anomaly** — `detect_decay(prev, cur)` flags an abnormal mean-motion
  increase rate (object losing altitude faster than routine drag), only over a
  ≥12 h epoch gap.
- **mission** — `classify_mission(name)` maps a satellite name to a mission
  family (military/recon/sar/navigation/earth_observation/weather/comms/
  commercial_imaging) via a public name-prefix lookup; never raises.

## Why this design

- **Pure functions on parsed elements.** Operates on the dict from
  `Tle.elements()` (inclination/RAAN/eccentricity/mean-motion + epoch from TLE
  line 2 fixed columns) — no SGP4 propagation needed, so it's cheap and
  unit-testable without a satellite catalog.
- **J2-corrected RAAN.** A raw RAAN delta is dominated by natural nodal
  regression; we subtract the expected J2 rate (`j2_raan_rate`, Vallado §9.4) and
  only flag the residual — otherwise every LEO object looks like it maneuvered.
- **Thresholds above noise, below secular drift** — clean-room constants from
  Lemmens & Krag (2014): period 0.1 min, inclination 0.05°, eccentricity 0.005,
  RAAN residual 0.5°, decay 0.01 rev/day². No ShadowBroker source copied.

## Key symbols

- `j2_raan_rate(inclination_deg, mean_motion_revday)` → deg/day (negative, prograde).
- `detect_maneuver(prev, cur)` / `detect_decay(prev, cur)` → alert dict or `None`.
- `classify_mission(name)` → `{"mission", "label"}`.
- Module-constant thresholds (`MANEUVER_*`, `DECAY_*`).

## Inputs / Outputs

- **In:** two element dicts (`Tle.elements()`), each with `epoch_ts`.
- **Out:** alert dicts with `type`, `reasons`/rates, `norad_id`, `epoch`; or
  `None` when within noise / insufficient epoch gap.

## Failure modes

- Missing element fields → returns `None` (never raises).
- `dt_days = 0` (same epoch) → RAAN check skipped, decay skipped.
- Garbage TLE line 2 → `Tle.elements()` returns `None`; caller skips the object.

## Cross-references

- Router: [backend-routers/satellites-router.md](../backend-routers/satellites-router.md) (`/api/satellites/anomalies`)
- Module: [backend/satellite-overpass.md](satellite-overpass.md) (`Tle.elements()`)
- Decision: [decisions/why-tle-history-for-maneuvers.md](../decisions/why-tle-history-for-maneuvers.md)
- Tests: [backend/tests/test_satellite_anomaly.py](../../backend/tests/test_satellite_anomaly.py)
- Runbook: [operations/satellite-overpass-runbook.md](../operations/satellite-overpass-runbook.md)
