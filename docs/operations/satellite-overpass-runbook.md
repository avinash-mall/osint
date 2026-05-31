# Operations — Satellite Overpass Prediction Runbook

Plan when a satellite next passes over an AOI, and draw its ground track, using
SGP4 over **analyst-imported** TLEs. The whole feature is offline computation —
the only thing that needs the outside world is getting fresh element sets *in*,
which is a manual air-gap import (see
[decisions/why-offline-tle-import.md](../decisions/why-offline-tle-import.md)).

Backed by [backend/satellite_overpass.py](../../backend/satellite_overpass.py) and
[backend-routers/satellites-router.md](../backend-routers/satellites-router.md);
the UI is the **"Sat"** tab in the map selection panel
([frontend/map-satellites-panel.md](../frontend/map-satellites-panel.md)).

## 1. Get TLEs onto the air-gapped host

TLEs are short-lived (accuracy degrades over days/weeks). On a connected
machine, download the relevant catalog as 2-/3-line text — e.g. CelesTrak's
`active.txt`, or a mission-specific group — then carry the file across the
air-gap on approved media.

A TLE set looks like (3-line form; the name line is optional):

```
ISS (ZARYA)
1 25544U 98067A   24029.51782528 -.00000857  00000-0 -78211-5 0  9994
2 25544  51.6442  21.4611 0005029 215.6310 144.4124 15.49521691210334
```

## 2. Import

**UI:** Map workspace → right rail → **Sat** tab → **TLE** button → paste the
text → **IMPORT**. The `N TLE` counter updates.

**API:**

```
POST /api/satellites/tle   {"text": "<raw 2-/3-line text>", "source": "celestrak-active-2026-05-31"}
GET  /api/satellites/tle   # list stored sets with epoch + provenance
```

Import upserts by NORAD id — re-importing a newer set for the same object
replaces it. `source` is a free-text provenance label; `epoch` is parsed from
line 1 so you can judge staleness later.

## 3. Predict overpasses

**UI:** pick an **Observer** point on the map (crosshair), set the **Window**
(hours) and **Min elev** sliders, press **PREDICT**. Each satellite with a pass
in the window lists its passes (AOS local time · max elevation · duration).
**TRACK** draws that satellite's ground track on the map (amber dashed line).

**API:**

```
POST /api/satellites/passes
{
  "aoi_id": 12,                 # OR "lat": 25.07, "lon": 55.18
  "hours": 24,                  # window length from now (or pass "start"/"end" ISO8601)
  "min_elevation_deg": 10,
  "step_s": 30
}

GET /api/satellites/ground-track/{norad_id}?hours=1.5&step_s=60
```

`/passes` resolves the observer from an `aoi_id` centroid or explicit `lat`/`lon`.
A pass is a contiguous run of samples above `min_elevation_deg`; AOS/LOS are
accurate to ±`step_s`.

## Tuning & interpretation

- **`min_elevation_deg`** — 10° is a sensible default horizon mask; raise to
  ~20-30° if low passes are not usable for your sensor geometry.
- **`step_s`** — 30 s balances accuracy vs cost; drop to 10 s for tighter AOS/LOS,
  raise for long multi-day windows.
- **Max elevation** is the headline quality number: a ~90° pass goes directly
  overhead; a 12° pass grazes the horizon.
- **Collection planning** — the AI router's `create_collection_requirement`
  already exists, so a high-elevation pass over an AOI is a natural cue to raise
  a requirement for that window.

## When results look wrong

- **No passes** → the window is too short, the elevation mask too high, or the
  satellite genuinely doesn't overfly the observer in that window. Widen `hours`
  or lower `min_elevation_deg`.
- **404 "no TLEs stored"** → import some (step 2).
- **A satellite silently missing from results** → its element set failed SGP4
  (decayed/garbage); the router skips it and logs a warning rather than
  fabricating a pass.
- **Positions drift from reality** → the TLE is stale. Check the displayed
  `epoch` and re-import a fresh set. There is no auto-refresh offline — freshness
  is an operator responsibility.

## Cross-references

- [backend/satellite-overpass.md](../backend/satellite-overpass.md)
- [backend-routers/satellites-router.md](../backend-routers/satellites-router.md)
- [decisions/why-offline-tle-import.md](../decisions/why-offline-tle-import.md)
- [frontend/map-satellites-panel.md](../frontend/map-satellites-panel.md)
- [backend/tracker-satellite.md](../backend/tracker-satellite.md) — the *other* "satellite pass" (detection association, not orbital mechanics)
