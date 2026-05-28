"""Auto-gate logic for GROUNDING_DINO.

GROUNDING_DINO is the most expensive specialist (~115 ms per call on real
DOTA chips). It only adds value when prompts include classes outside the
standard SAM3 common vocabulary (DOTA-v1 + generic geo terms + the
sensor-default prompts the backend ontology supplies). For prompts entirely
within this "common vocab", SAM3 alone (with its own pretrained text
encoder) handles them well, so GROUNDING_DINO is a wasted ~115 ms per
request.

This module decides whether a request's prompts justify running GROUNDING_DINO.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

import requests

logger = logging.getLogger(__name__)

# DOTA-v1.0 class names (matches Ultralytics' DOTA-v1 head; included in addition
# to the backend ontology so that DOTA labels won't trigger Grounding DINO
# unnecessarily).
_DOTA_CLASSES = {
    "plane", "ship", "storage tank", "storage-tank",
    "baseball diamond", "baseball-diamond", "tennis court", "tennis-court",
    "basketball court", "basketball-court", "ground track field", "ground-track-field",
    "harbor", "bridge", "large vehicle", "large-vehicle",
    "small vehicle", "small-vehicle", "helicopter", "roundabout",
    "soccer ball field", "soccer-ball-field", "swimming pool", "swimming-pool",
    "container crane", "container-crane", "airport", "helipad",
}

# Generic geographic / ground-cover terms that SAM3 handles well
# (relevant to PRITHVI/multispectral pipelines).
_GEO_TERMS = {
    "water", "vegetation", "field", "land", "ground",
    "forest", "tree", "river", "lake", "sea", "ocean",
    "road", "building", "rooftop", "vehicle", "boat",
}

_ONTOLOGY_BACKEND_URL = os.getenv("ONTOLOGY_BACKEND_URL", "http://backend:8080")
_ONTOLOGY_VOCAB_TTL = 300.0  # 5 min — the gate doesn't change every request
_ONTOLOGY_VOCAB_LOCK = threading.Lock()
_ONTOLOGY_VOCAB_CACHE: dict[str, object] = {"ts": 0.0, "vocab": frozenset()}


def _fetch_ontology_vocab() -> frozenset[str]:
    """Fetch the union of optical+multispectral+sar default prompts from the
    backend ontology API. Used as the dynamic part of the common vocabulary.

    Cached for _ONTOLOGY_VOCAB_TTL seconds. On any error (backend down,
    timeout) returns the previously cached vocab — empty on first failure.
    The gate degrades gracefully: an empty ontology vocab means more requests
    fall through to GROUNDING_DINO, which is conservative (more recall, more
    latency).
    """
    now = time.time()
    with _ONTOLOGY_VOCAB_LOCK:
        cached_ts = float(_ONTOLOGY_VOCAB_CACHE["ts"])  # type: ignore[arg-type]
        cached_vocab = _ONTOLOGY_VOCAB_CACHE["vocab"]
        if cached_vocab and (now - cached_ts) < _ONTOLOGY_VOCAB_TTL:
            return cached_vocab  # type: ignore[return-value]
    # Network fetch happens outside the lock to avoid blocking concurrent
    # readers while the (possibly slow) backend round-trips complete.
    merged: set[str] = set()
    try:
        for sensor in ("optical", "multispectral", "sar"):
            resp = requests.get(
                f"{_ONTOLOGY_BACKEND_URL}/api/ontology/default-prompts",
                params={"sensor": sensor},
                timeout=5.0,
            )
            resp.raise_for_status()
            for p in resp.json().get("prompts", []):
                if isinstance(p, str):
                    merged.add(p.strip().lower())
        vocab = frozenset(merged)
        with _ONTOLOGY_VOCAB_LOCK:
            _ONTOLOGY_VOCAB_CACHE["ts"] = time.time()
            _ONTOLOGY_VOCAB_CACHE["vocab"] = vocab
        return vocab
    except Exception as exc:
        logger.warning(
            "grounding_dino_gate: failed to refresh ontology vocab: %s "
            "(falling back to %d cached / static terms)",
            exc, len(cached_vocab),  # type: ignore[arg-type]
        )
        return cached_vocab  # type: ignore[return-value]


# Static portion of the common vocabulary — always available even when the
# backend is offline.
_STATIC_VOCAB: frozenset[str] = (
    frozenset(p.lower() for p in _DOTA_CLASSES)
    | frozenset(p.lower() for p in _GEO_TERMS)
)


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if t]


def _common_vocab() -> frozenset[str]:
    """Effective common vocab = static (DOTA + geo) ∪ backend ontology defaults."""
    return _STATIC_VOCAB | _fetch_ontology_vocab()


def _vocab_token_sets() -> list[frozenset[str]]:
    """Tokenise vocab terms for subset-matching (cached per vocab refresh)."""
    return [frozenset(_tokens(term)) for term in _common_vocab() if term]


def is_common(prompt: str) -> bool:
    """Return True if `prompt` is in the SAM3 common vocabulary.

    Matching strategy:
      1. Exact case-insensitive match against the vocab.
      2. Token-level match: the prompt's word tokens must be a subset of (or
         exactly equal to) some vocab entry's tokens. This handles "main
         battle tank" → "tank" without false-matching gibberish like
         "zxqkk_unicorn_battalion" against short vocab terms like "lion".
    """
    p = prompt.strip().lower()
    if not p:
        return True  # empty prompt → no signal that GDINO is needed
    vocab = _common_vocab()
    if p in vocab:
        return True
    prompt_tokens = set(_tokens(p))
    if not prompt_tokens:
        return True
    for vocab_tokens in _vocab_token_sets():
        # Subset matching is symmetric on purpose: prompt "main battle
        # tank" → vocab "tank" (vocab ⊂ prompt), and prompt "tank" →
        # vocab "main battle tank" (prompt ⊂ vocab) both gate as common.
        # The trade-off is "tanker" can match "tank" since "tank" tokens
        # ⊂ "tanker" tokens is false ("tanker" ≠ "tank") — token equality
        # only fires when both word lists agree.
        if vocab_tokens and (
            vocab_tokens.issubset(prompt_tokens) or prompt_tokens.issubset(vocab_tokens)
        ):
            return True
    return False


def should_run_grounding_dino(
    prompts: list[str],
    *,
    force: bool = False,
) -> tuple[bool, str | None]:
    """Decide whether to invoke GROUNDING_DINO for this request.

    Args:
        prompts: text prompts that will be sent to SAM3.
        force: when True, bypass the gate and always run.

    Returns:
        (should_run, gated_reason)
        - should_run: whether to invoke GROUNDING_DINO.
        - gated_reason: short string explaining why it was skipped, or None
          when it ran. Surfaced in the API response as `grounding_dino_gated`.
    """
    if force:
        return True, None
    if not prompts:
        return False, "no_prompts"
    uncommon = [p for p in prompts if not is_common(p)]
    if not uncommon:
        return False, "all_prompts_in_common_vocab"
    return True, None


def common_vocab_size() -> int:
    return len(_common_vocab())


def reload_vocab() -> None:
    """Force refresh of the ontology vocab cache on next call to is_common()."""
    with _ONTOLOGY_VOCAB_LOCK:
        _ONTOLOGY_VOCAB_CACHE["ts"] = 0.0
        _ONTOLOGY_VOCAB_CACHE["vocab"] = frozenset()
    logger.info(
        "grounding_dino_gate: vocab cache cleared (SIGHUP or manual reload)"
    )


# ---------------------------------------------------------------------------
# SIGHUP -> reload_vocab() handler. Installed once at module import.
# Wrapped in a sentinel so reimports (e.g. by reload tools) don't re-register.
# ---------------------------------------------------------------------------
import signal as _signal  # noqa: E402

_SIGHUP_INSTALLED = False


def _install_sighup_handler() -> None:
    """Install SIGHUP handler if running in main thread.

    signal.signal() must be called from the main thread; in non-main-thread
    contexts (some uvicorn worker configurations, embedded interpreters)
    this raises ValueError. We log and continue — the manual ``reload_vocab()``
    helper still works via direct calls.
    """
    global _SIGHUP_INSTALLED
    if _SIGHUP_INSTALLED:
        return
    try:
        _signal.signal(
            _signal.SIGHUP, lambda signum, frame: reload_vocab()
        )
        _SIGHUP_INSTALLED = True
        logger.info(
            "grounding_dino_gate: SIGHUP -> reload_vocab() handler installed"
        )
    except (ValueError, OSError, AttributeError) as exc:
        logger.warning(
            "grounding_dino_gate: could not install SIGHUP handler: %s", exc
        )


_install_sighup_handler()
