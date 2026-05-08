"""Health wait helpers for the always-on SAM3 inference service."""

from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

PROVIDER = "sam3"
HEALTH_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")

START_TIMEOUT_S = int(os.getenv("PROVIDER_START_TIMEOUT_S", "120"))
HEALTH_POLL_INTERVAL_S = float(os.getenv("PROVIDER_HEALTH_POLL_INTERVAL_S", "2"))


def _wait_for_health(deadline: float) -> None:
    health_url = f"{HEALTH_URL.rstrip('/')}/health"
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            resp = requests.get(health_url, timeout=3)
            if resp.status_code == 200:
                return
            last_error = RuntimeError(f"HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last_error = exc
        time.sleep(HEALTH_POLL_INTERVAL_S)
    raise TimeoutError(f"Provider {PROVIDER} did not become healthy at {health_url}: {last_error}")


def ensure_running() -> None:
    """Wait for the Compose-managed SAM3 service to answer /health."""
    _wait_for_health(time.time() + START_TIMEOUT_S)


def mark_active() -> None:
    """Compatibility hook retained for callers that mark inference activity."""
    return None
