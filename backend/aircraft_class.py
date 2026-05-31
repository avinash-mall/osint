"""ICAO aircraft type-code → coarse airframe class (R4) — offline reference.

A clean-room lookup mapping ICAO type designators (e.g. ``A320``, ``B06``,
``C130``) to a coarse airframe shape: ``heli`` / ``turboprop`` / ``bizjet`` /
``airliner``. Adapted in concept from ShadowBroker's ADS-B styling table; this
is a pure, dependency-free reference written from public ICAO designators.

**Why backend, not UI:** Sentinel does not track live ADS-B, so no current code
path carries an ICAO type code. Rather than ship unused UI, this lands as a
reusable helper — when an aircraft `operational_entity` (or a future ADS-B
import) carries an ICAO type in its metadata, `classify_airframe(code)` gives a
coarse shape for display/grouping. See docs/backend/aircraft-class.md.
"""

from __future__ import annotations

from typing import Optional

# Representative ICAO type designators per coarse class. Not exhaustive — extend
# as needed; unknown codes fall through to the category hint or "unknown".
_HELI = {
    "R22", "R44", "R66", "B06", "B47", "B105", "B212", "B412", "B429",
    "A109", "A139", "A169", "EC30", "EC35", "EC45", "EC75", "H125", "H130",
    "H145", "H160", "H175", "H225", "S70", "S76", "S92", "AS50", "BK17",
    "CH47", "UH60", "AH64", "Z9",
}
_TURBOPROP = {
    "C208", "PC12", "TBM7", "TBM8", "TBM9", "AT72", "AT75", "AT76", "DH8A",
    "DH8B", "DH8C", "DH8D", "B350", "BE20", "B190", "SF34", "E110", "C130",
    "A400", "C295", "AN12", "AN26", "P3", "P8",  # patrol/transport props (+ P8 is a jet but mil-patrol; kept coarse)
}
_BIZJET = {
    "GLF4", "GLF5", "GLF6", "GLEX", "G280", "C25A", "C25B", "C25C", "C500",
    "C510", "C525", "C550", "C560", "C56X", "C650", "C680", "C700", "C750",
    "CL30", "CL35", "CL60", "E55P", "E50P", "FA7X", "FA8X", "F2TH", "F900",
    "LJ45", "LJ60", "LJ75", "H25B", "PRM1", "BE40",
}


def classify_airframe(type_code: Optional[str], category: Optional[str] = None) -> str:
    """Coarse airframe class from an ICAO type designator.

    ``category`` is an optional ADS-B emitter-category hint (e.g. ``A7`` =
    rotorcraft) used only as a fallback when the type code is unknown. Returns
    one of ``heli`` / ``turboprop`` / ``bizjet`` / ``airliner`` / ``unknown``.
    Never raises.
    """
    if type_code:
        code = type_code.strip().upper()
        if code in _HELI:
            return "heli"
        if code in _TURBOPROP:
            return "turboprop"
        if code in _BIZJET:
            return "bizjet"
        # Heuristic: A3xx/A2xx/B7xx/B7x7 families are airliners.
        if (code.startswith(("A3", "A2", "B7", "E1", "E2", "E19", "CRJ", "MD", "DC")) and code not in _BIZJET):
            return "airliner"
    if category:
        cat = category.strip().upper()
        if cat in {"A7"}:  # ADS-B emitter category A7 = rotorcraft
            return "heli"
        if cat in {"A1"}:  # light
            return "bizjet"
        if cat in {"A3", "A4", "A5"}:  # large / heavy
            return "airliner"
    return "unknown"
