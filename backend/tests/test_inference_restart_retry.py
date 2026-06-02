"""Unit tests for inference-restart-resilient chip POST retry.

When inference-sam3 hits a poisoned CUDA context it self-heals by exiting so
compose respawns it with a clean context (~100 s). During that window chip
POSTs fail at the connection level. These tests cover the worker's classifier
and retry-then-recover path that makes a self-heal restart transparent instead
of silently scoring the rest of the scene as zero-detection chips.

See docs/decisions/why-retry-chips-across-inference-restart.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import worker_legacy as worker  # noqa: E402


class _Resp:
    """Minimal stand-in for a requests.Response (attaches itself to HTTPError
    exactly like requests does, so the classifier can read status_code)."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        return self._payload


def _http_error(status):
    err = requests.exceptions.HTTPError(f"HTTP {status}")
    err.response = _Resp({}, status=status)
    return err


def test_unavailable_classifier():
    # A down/restarting service surfaces as ConnectionError (covers ConnectTimeout)
    # or a mid-stream ChunkedEncodingError.
    assert worker._inference_unavailable(requests.exceptions.ConnectionError("refused"))
    assert worker._inference_unavailable(requests.exceptions.ConnectTimeout("connect timeout"))
    assert worker._inference_unavailable(requests.exceptions.ChunkedEncodingError("aborted"))
    # Model still preloading after a restart → 503 (and proxy 502/504) are retriable.
    assert worker._inference_unavailable(_http_error(503))
    assert worker._inference_unavailable(_http_error(502))
    assert worker._inference_unavailable(_http_error(504))
    # Per-chip faults are NOT a down service — must not trigger a wait+retry.
    assert not worker._inference_unavailable(requests.exceptions.ReadTimeout("slow forward"))
    assert not worker._inference_unavailable(_http_error(500))
    assert not worker._inference_unavailable(_http_error(404))
    assert not worker._inference_unavailable(requests.exceptions.HTTPError("no response attr"))
    assert not worker._inference_unavailable(ValueError("bad json"))


def test_retry_recovers_after_restart(monkeypatch):
    monkeypatch.setattr(worker, "INFERENCE_RESTART_RETRY_MAX", 3)
    waited = {"n": 0}

    def _fake_wait(*_a, **_k):
        waited["n"] += 1
        return True

    monkeypatch.setattr(worker, "_wait_for_inference_healthy", _fake_wait)

    calls = {"n": 0}

    def send():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("Connection refused")
        return _Resp({"detections": [1, 2]})

    out = worker._post_chip_with_restart_retry(send, "chip x=0 y=0")
    assert out == {"detections": [1, 2]}
    assert calls["n"] == 2          # failed once, retried once, succeeded
    assert waited["n"] == 1         # waited for recovery exactly once


def test_retry_gives_up_when_service_never_recovers(monkeypatch):
    monkeypatch.setattr(worker, "INFERENCE_RESTART_RETRY_MAX", 2)
    monkeypatch.setattr(worker, "_wait_for_inference_healthy", lambda *a, **k: False)

    def send():
        raise requests.exceptions.ConnectionError("name resolution failed")

    assert worker._post_chip_with_restart_retry(send, "chip") is None


def test_retry_budget_exhausted(monkeypatch):
    monkeypatch.setattr(worker, "INFERENCE_RESTART_RETRY_MAX", 2)
    monkeypatch.setattr(worker, "_wait_for_inference_healthy", lambda *a, **k: True)

    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise requests.exceptions.ConnectionError("refused")

    assert worker._post_chip_with_restart_retry(send, "chip") is None
    # initial attempt + INFERENCE_RESTART_RETRY_MAX retries = 3 sends
    assert calls["n"] == 3


def test_per_chip_error_not_retried(monkeypatch):
    # A ReadTimeout is per-chip slowness, not a down service: no wait, no retry.
    waited = {"n": 0}
    monkeypatch.setattr(
        worker,
        "_wait_for_inference_healthy",
        lambda *a, **k: waited.__setitem__("n", waited["n"] + 1) or True,
    )

    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise requests.exceptions.ReadTimeout("slow")

    assert worker._post_chip_with_restart_retry(send, "chip") is None
    assert calls["n"] == 1
    assert waited["n"] == 0


def test_http_500_not_retried_as_unavailable(monkeypatch):
    # A 500 from a *reachable* service is a per-chip failure, returned as None
    # without waiting for a restart.
    waited = {"n": 0}
    monkeypatch.setattr(
        worker,
        "_wait_for_inference_healthy",
        lambda *a, **k: waited.__setitem__("n", waited["n"] + 1) or True,
    )

    def send():
        return _Resp({"error": "boom"}, status=500)

    assert worker._post_chip_with_restart_retry(send, "chip") is None
    assert waited["n"] == 0


def test_503_during_preload_is_retried(monkeypatch):
    # Container back up but model still preloading → /detect_raw 503. The chip
    # must wait for model_loaded then retry, not give up.
    monkeypatch.setattr(worker, "INFERENCE_RESTART_RETRY_MAX", 3)
    waited = {"n": 0}
    monkeypatch.setattr(
        worker,
        "_wait_for_inference_healthy",
        lambda *a, **k: waited.__setitem__("n", waited["n"] + 1) or True,
    )

    calls = {"n": 0}

    def send():
        calls["n"] += 1
        if calls["n"] <= 2:
            return _Resp({"detail": "model loading"}, status=503)
        return _Resp({"detections": []})

    out = worker._post_chip_with_restart_retry(send, "chip")
    assert out == {"detections": []}
    assert calls["n"] == 3   # 503, 503, then 200
    assert waited["n"] == 2   # waited before each retry
