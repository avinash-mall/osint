"""Unit tests for backend/routing.py — OSRM HTTP client, no live service."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch


def _reload_routing(monkeypatch, *, osrm_url: str = "http://osrm:5000"):
    import sys
    monkeypatch.setenv("OSRM_URL", osrm_url)
    sys.modules.pop("routing", None)
    routing = importlib.import_module("routing")
    routing.reset_osrm_health_cache()
    return routing


def _resp(status: int, payload):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=payload)
    r.text = str(payload)
    return r


def test_osrm_available_true_when_service_ok(monkeypatch):
    routing = _reload_routing(monkeypatch)
    with patch.object(routing.requests, "get", return_value=_resp(200, {"code": "Ok"})) as get:
        assert routing.osrm_available() is True
        assert get.call_count == 1


def test_osrm_available_true_for_noroute_code(monkeypatch):
    # A reachable OSRM that simply couldn't route 0,0 → 0.001,0.001 is still
    # considered available — NoRoute is a service-up answer, not an outage.
    routing = _reload_routing(monkeypatch)
    with patch.object(routing.requests, "get", return_value=_resp(200, {"code": "NoRoute"})):
        assert routing.osrm_available() is True


def test_osrm_available_false_on_http_error(monkeypatch):
    routing = _reload_routing(monkeypatch)
    with patch.object(routing.requests, "get", return_value=_resp(503, {})):
        assert routing.osrm_available() is False


def test_osrm_available_false_on_exception(monkeypatch):
    routing = _reload_routing(monkeypatch)
    with patch.object(routing.requests, "get", side_effect=RuntimeError("connection refused")):
        assert routing.osrm_available() is False


def test_osrm_available_caches_within_ttl(monkeypatch):
    routing = _reload_routing(monkeypatch)
    with patch.object(routing.requests, "get", return_value=_resp(200, {"code": "Ok"})) as get:
        routing.osrm_available()
        routing.osrm_available()
        routing.osrm_available()
        # Three consecutive probes within the TTL window collapse to one HTTP
        # call — this is the contract that keeps /api/analytics/capabilities
        # cheap.
        assert get.call_count == 1


def test_compute_routes_returns_none_when_osrm_unavailable(monkeypatch):
    routing = _reload_routing(monkeypatch)
    with patch.object(routing.requests, "get", return_value=_resp(503, {})):
        out = routing.compute_routes(0.0, 0.0, 1.0, 1.0)
    assert out is None


def test_compute_routes_returns_features_on_ok(monkeypatch):
    routing = _reload_routing(monkeypatch)
    health = _resp(200, {"code": "Ok"})
    route_payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 12345.0,
                "duration": 600.0,
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
            },
            {
                "distance": 13000.0,
                "duration": 700.0,
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]]},
            },
        ],
    }
    route = _resp(200, route_payload)
    with patch.object(routing.requests, "get", side_effect=[health, route]):
        out = routing.compute_routes(0.0, 0.0, 1.0, 1.0, strategy="shortest")

    assert isinstance(out, list)
    assert len(out) == 2
    first = out[0]
    assert first["type"] == "Feature"
    assert first["geometry"]["type"] == "LineString"
    assert first["properties"]["option"] == 1
    assert first["properties"]["strategy"] == "shortest"
    assert first["properties"]["label"] == "shortest"
    assert first["properties"]["length_m"] == 12345.0
    assert first["properties"]["duration_minutes"] == 10.0
    assert first["properties"]["risk"] == "primary"
    assert out[1]["properties"]["risk"] == "alternative 2"


def test_compute_routes_skips_degenerate_geometry(monkeypatch):
    routing = _reload_routing(monkeypatch)
    payload = {
        "code": "Ok",
        "routes": [
            {"distance": 0.0, "duration": 0.0, "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0]]}},
        ],
    }
    with patch.object(routing.requests, "get", side_effect=[_resp(200, {"code": "Ok"}), _resp(200, payload)]):
        out = routing.compute_routes(0.0, 0.0, 1.0, 1.0)
    assert out is None


def test_compute_routes_returns_none_on_noroute(monkeypatch):
    routing = _reload_routing(monkeypatch)
    with patch.object(routing.requests, "get", side_effect=[
        _resp(200, {"code": "Ok"}),
        _resp(200, {"code": "NoRoute", "message": "Impossible route between points"}),
    ]):
        out = routing.compute_routes(0.0, 0.0, 1.0, 1.0)
    assert out is None
