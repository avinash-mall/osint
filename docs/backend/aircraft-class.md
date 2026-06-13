# `backend/aircraft_class.py` — ICAO type → coarse airframe class

**Path:** [backend/aircraft_class.py](../../backend/aircraft_class.py)
**Lines:** ~67
**Depends on:** nothing (stdlib only). Pure function.

## Purpose

Map an ICAO aircraft type designator (e.g. `A320`, `B06`, `C130`) to a coarse
airframe class — `heli` / `turboprop` / `bizjet` / `airliner` / `unknown` — for
display or grouping. Clean-room from public ICAO designators; adapted in concept
from ShadowBroker's ADS-B styling table.

## Why this design

- **Backend reference, not UI (deliberate).** Sentinel does not track live ADS-B,
  so **no current code path carries an ICAO type code** — aircraft are DOTA
  ontology classes (helicopter/plane) without type designators. Shipping a
  frontend chip would be dead UI, so per the round-2 plan this landed as a
  reusable, tested helper instead. When an aircraft `operational_entity` or a
  future ADS-B import carries an ICAO type in metadata, callers get a coarse
  shape for free.
- **Lookup + heuristic + category fallback.** Exact sets for heli/turboprop/
  bizjet; an `A3xx`/`B7xx`/`E1xx`… prefix heuristic for airliners; an optional
  ADS-B emitter-category (`A7`=rotorcraft, etc.) fallback when the type is
  unknown. Never raises.

## Key symbols

- `classify_airframe(type_code, category=None) -> str`.
- Module sets `_HELI` / `_TURBOPROP` / `_BIZJET` (representative, extend as needed).

## Inputs / Outputs

- **In:** ICAO type designator (case-insensitive), optional ADS-B category hint.
- **Out:** `"heli" | "turboprop" | "bizjet" | "airliner" | "unknown"`.

## Failure modes

- Unknown / empty / `None` code with no usable category → `"unknown"`.

## Cross-references

- Tests: [backend/tests/test_aircraft_class.py](../../backend/tests/test_aircraft_class.py)
- Aircraft ontology classes: `backend/scripts/seeds/defenceOntology.seed.json`
