"""Auto-gate logic for GROUNDING_DINO.

GROUNDING_DINO is the most expensive specialist (~115 ms per call on real
DOTA chips). It only adds value when prompts include classes outside the
standard SAM3 ground_v1 vocabulary (576 COCO/Objects365/LVIS classes) and
the DOTA-v1 class set. For prompts entirely within this "common vocab",
SAM3 alone (with its own pretrained text encoder) handles them well, so
GROUNDING_DINO is a wasted ~115 ms per request.

This module decides whether a request's prompts justify running GROUNDING_DINO.
"""
from __future__ import annotations

import json
from pathlib import Path

# DOTA-v1.0 class names (matches Ultralytics' DOTA-v1 head; included in addition
# to ground_v1 so that DOTA labels won't trigger Grounding DINO unnecessarily).
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


def _load_ground_v1() -> frozenset[str]:
    path = Path(__file__).parent / "prompts" / "ground_v1_full.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(p.strip().lower() for p in data.get("prompts", []) if isinstance(p, str))
    except Exception:
        return frozenset()


# Module-level cache of the common vocabulary (computed once at import).
_COMMON_VOCAB: frozenset[str] = (
    _load_ground_v1()
    | frozenset(p.lower() for p in _DOTA_CLASSES)
    | frozenset(p.lower() for p in _GEO_TERMS)
)


import re

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if t]


# Pre-tokenise the vocab once so per-request gating stays cheap.
_VOCAB_TOKEN_SETS: list[frozenset[str]] = [
    frozenset(_tokens(term)) for term in _COMMON_VOCAB if term
]


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
    if p in _COMMON_VOCAB:
        return True
    prompt_tokens = set(_tokens(p))
    if not prompt_tokens:
        return True
    for vocab_tokens in _VOCAB_TOKEN_SETS:
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
    return len(_COMMON_VOCAB)
