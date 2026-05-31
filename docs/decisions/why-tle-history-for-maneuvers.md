# Decision — keep per-epoch TLE history for maneuver/decay detection

## Context

A1 stores TLEs in `satellite_tles` keyed by `norad_id` with **latest-wins**
semantics (re-import overwrites). That's right for overpass prediction (you want
the freshest elements) but it destroys the *previous* epoch — so it cannot
support maneuver/decay detection, which is fundamentally a comparison **between**
epochs. ShadowBroker keeps a TLE history snapshot for exactly this.

## Decision

Add a separate `satellite_tle_history` table (`norad_id, epoch, name, line1,
line2, imported_at`, PK `(norad_id, epoch)`) written alongside the
`satellite_tles` upsert on every import. `GET /api/satellites/anomalies`
compares the two most-recent epochs per object via the pure functions in
`satellite_anomaly.py`. The latest-wins `satellite_tles` semantics are unchanged.

## Why

- **Don't break overpass.** A history table is additive; mutating
  `satellite_tles` to retain old rows would complicate the "freshest element"
  read that `predict_passes` / `ground_track` rely on.
- **Idempotent.** PK `(norad_id, epoch)` + `ON CONFLICT DO NOTHING` means
  re-importing the same file is a no-op; only genuinely new epochs accumulate.
- **Offline.** Detection is pure math over stored elements — no network, no
  propagation (Hard rule #8). Clean-room; no ShadowBroker source copied.

## Consequences

- History grows by one row per object per distinct epoch imported. Unbounded in
  principle; in practice analysts import a catalog occasionally, not continuously.
  A retention prune can be added later if needed (not required now).
- Anomaly detection needs **≥2 distinct epochs** for an object; with a single
  import `/anomalies` returns empty (`objects_compared: 0`).

## Cross-references

- [backend/satellite-anomaly.md](../backend/satellite-anomaly.md)
- [backend-routers/satellites-router.md](../backend-routers/satellites-router.md)
- [decisions/why-offline-tle-import.md](why-offline-tle-import.md) (the A1 storage decision this builds on)
