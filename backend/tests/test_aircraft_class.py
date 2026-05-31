"""Unit tests for the ICAO airframe classifier (R4) — pure, offline."""

from __future__ import annotations

from aircraft_class import classify_airframe


def test_helicopter_codes():
    assert classify_airframe("H145") == "heli"
    assert classify_airframe("UH60") == "heli"
    assert classify_airframe("R44") == "heli"


def test_bizjet_codes():
    assert classify_airframe("GLF6") == "bizjet"
    assert classify_airframe("C750") == "bizjet"


def test_turboprop_codes():
    assert classify_airframe("C208") == "turboprop"
    assert classify_airframe("AT76") == "turboprop"


def test_airliner_heuristic():
    assert classify_airframe("A320") == "airliner"
    assert classify_airframe("B738") == "airliner"
    assert classify_airframe("E190") == "airliner"


def test_unknown_and_none():
    assert classify_airframe("ZZZZ") == "unknown"
    assert classify_airframe(None) == "unknown"
    assert classify_airframe("") == "unknown"


def test_category_fallback():
    # Unknown type code, but ADS-B emitter category disambiguates.
    assert classify_airframe(None, category="A7") == "heli"
    assert classify_airframe("ZZZZ", category="A3") == "airliner"


def test_case_insensitive_and_whitespace():
    assert classify_airframe("  h145 ") == "heli"
