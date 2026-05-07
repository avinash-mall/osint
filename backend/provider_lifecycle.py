"""Dynamic lifecycle management for inference provider containers.

Inference services are gated by docker-compose profiles so they don't run by
default. This module starts the containers selected at upload time, tracks
last-use timestamps in Redis, and stops idle containers after a cooldown.

One-time setup required: `docker compose --profile all create` to materialize
the containers in stopped state. Subsequent start/stop/inspect happens via the
Docker Engine API (the `docker` Python SDK), which avoids needing the compose
CLI inside the backend image.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

PROVIDER_TO_SERVICE = {
    "yolo": "inference",
    "lae-dino": "inference-lae-dino",
    "mmrotate": "inference-mmrotate",
    "lsknet": "inference-lsknet",
    "sam2": "inference-sam2",
    "sam3": "inference-sam3",
}

PROVIDER_HEALTH_URLS = {
    "yolo": os.getenv("INFERENCE_URL", "http://inference:8001"),
    "lae-dino": os.getenv("INFERENCE_LAE_DINO_URL", "http://inference-lae-dino:8001"),
    "mmrotate": os.getenv("INFERENCE_MMROTATE_URL", "http://inference-mmrotate:8001"),
    "lsknet": os.getenv("INFERENCE_LSKNET_URL", "http://inference-lsknet:8001"),
    "sam2": os.getenv("INFERENCE_SAM2_URL", "http://inference-sam2:8001"),
    "sam3": os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001"),
}

LIFECYCLE_ENABLED = os.getenv("PROVIDER_LIFECYCLE_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
COMPOSE_PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "osint")
START_TIMEOUT_S = int(os.getenv("PROVIDER_START_TIMEOUT_S", "120"))
HEALTH_POLL_INTERVAL_S = float(os.getenv("PROVIDER_HEALTH_POLL_INTERVAL_S", "2"))
IDLE_COOLDOWN_S = int(os.getenv("PROVIDER_IDLE_COOLDOWN_S", "600"))
REDIS_LAST_USED_KEY = "provider:last_used:{name}"


def _docker_client():
    import docker
    return docker.from_env()


def _redis_client():
    import redis
    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _container_name_candidates(service: str) -> list[str]:
    return [
        f"{COMPOSE_PROJECT}-{service}-1",
        f"{COMPOSE_PROJECT}_{service}_1",
        service,
    ]


def _find_container(client, service: str):
    from docker.errors import NotFound
    last_err: Exception | None = None
    for name in _container_name_candidates(service):
        try:
            return client.containers.get(name)
        except NotFound as exc:
            last_err = exc
            continue
    raise RuntimeError(
        f"No container found for service '{service}'. Run "
        f"`docker compose --profile all create` once to materialize it. "
        f"(last error: {last_err})"
    )


def _wait_for_health(provider: str, deadline: float) -> None:
    base_url = PROVIDER_HEALTH_URLS[provider]
    health_url = f"{base_url.rstrip('/')}/health"
    while time.time() < deadline:
        try:
            resp = requests.get(health_url, timeout=3)
            if resp.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(HEALTH_POLL_INTERVAL_S)
    raise TimeoutError(f"Provider {provider} did not become healthy at {health_url} before deadline")


def ensure_running(providers: Iterable[str]) -> None:
    """Start the containers backing the given providers and wait for /health.

    No-op if PROVIDER_LIFECYCLE_ENABLED=false (legacy / dev mode where all
    services are started by `docker compose --profile all up`).
    """
    if not LIFECYCLE_ENABLED:
        return
    requested = [p for p in providers if p in PROVIDER_TO_SERVICE]
    if not requested:
        return

    client = _docker_client()
    deadline = time.time() + START_TIMEOUT_S
    needs_health: list[str] = []
    for provider in requested:
        service = PROVIDER_TO_SERVICE[provider]
        container = _find_container(client, service)
        container.reload()
        if container.status != "running":
            logger.info("[LIFECYCLE] starting %s (provider=%s, was %s)", service, provider, container.status)
            container.start()
            needs_health.append(provider)
        else:
            try:
                resp = requests.get(f"{PROVIDER_HEALTH_URLS[provider].rstrip('/')}/health", timeout=2)
                if resp.status_code != 200:
                    needs_health.append(provider)
            except requests.RequestException:
                needs_health.append(provider)

    for provider in needs_health:
        _wait_for_health(provider, deadline)
        logger.info("[LIFECYCLE] %s ready", provider)


def mark_active(providers: Iterable[str]) -> None:
    if not LIFECYCLE_ENABLED:
        return
    try:
        r = _redis_client()
        now = int(time.time())
        for provider in providers:
            if provider not in PROVIDER_TO_SERVICE:
                continue
            r.set(REDIS_LAST_USED_KEY.format(name=provider), now)
    except Exception as exc:
        logger.warning("[LIFECYCLE] mark_active failed: %s", exc)


def stop_idle(cooldown_s: int | None = None) -> list[str]:
    """Stop any provider container whose last-used timestamp is older than
    cooldown_s. Providers that have never been marked active are skipped (so
    a freshly-deployed system doesn't immediately stop everything)."""
    if not LIFECYCLE_ENABLED:
        return []
    cooldown = cooldown_s if cooldown_s is not None else IDLE_COOLDOWN_S
    cutoff = time.time() - cooldown
    stopped: list[str] = []
    try:
        r = _redis_client()
    except Exception as exc:
        logger.warning("[LIFECYCLE] stop_idle redis unavailable: %s", exc)
        return stopped

    client = _docker_client()
    for provider, service in PROVIDER_TO_SERVICE.items():
        last_used_raw = r.get(REDIS_LAST_USED_KEY.format(name=provider))
        if last_used_raw is None:
            continue
        try:
            last_used = float(last_used_raw)
        except (TypeError, ValueError):
            continue
        if last_used > cutoff:
            continue
        try:
            container = _find_container(client, service)
            container.reload()
            if container.status == "running":
                logger.info("[LIFECYCLE] stopping idle %s (last_used=%s, cooldown=%ss)", service, int(last_used), cooldown)
                container.stop(timeout=30)
                stopped.append(provider)
        except Exception as exc:
            logger.warning("[LIFECYCLE] stop_idle on %s failed: %s", service, exc)
    return stopped
