"""Auto-gate logic for the FAIR1M-OBB specialist.

FAIR1M-OBB is a fine-grained aerial OBB detector with 37 sub-class
labels (Boeing 737/747/777/787, A220/A321/A330/A350, ARJ21, Cessna,
Warship, Tugboat, Dump Truck, Tractor, ...). It only adds signal when
the request's prompts mention vocabulary FAIR1M can actually emit. If
every prompt is already covered by the DOTA-v1 head (plane, ship,
helicopter, ...) FAIR1M just duplicates work and risks confusing NMS.

Decision logic (mirrors ``grounding_dino_gate.py``):

    fire = ANY prompt overlaps FAIR1M_VOCAB AND NOT in _DOTA_CLASSES
         | metadata.force_fair1m_obb == True

The DOTA exclusion is the symmetric of why DOTA-OBB stays gated to
DOTA-relevant prompts (``_prompts_relevant_to_dota`` in main.py): each
specialist runs only when its vocabulary differentially helps.
"""
from __future__ import annotations

import logging
import re

from fair1m_obb import FAIR1M_CLASSES

logger = logging.getLogger(__name__)


# DOTA-v1.0 class names; duplicated from grounding_dino_gate.py so the
# two modules don't form an import cycle. Keep in sync.
_DOTA_CLASSES: frozenset[str] = frozenset({
    "plane", "ship", "storage tank", "storage-tank",
    "baseball diamond", "baseball-diamond", "tennis court", "tennis-court",
    "basketball court", "basketball-court", "ground track field", "ground-track-field",
    "harbor", "bridge", "large vehicle", "large-vehicle",
    "small vehicle", "small-vehicle", "helicopter", "roundabout",
    "soccer ball field", "soccer-ball-field", "swimming pool", "swimming-pool",
    "container crane", "container-crane", "airport", "helipad",
})


def _vocab_variants(label: str) -> set[str]:
    """Expand a label into matching variants (space- and dash- forms)."""
    base = label.strip().lower()
    if not base:
        return set()
    variants = {base, base.replace(" ", "-"), base.replace("-", " ")}
    # Strip the GaoFen "other-*" prefix variant so prompt "airplane" matches
    # "other-airplane" via the substring path below; the variant set already
    # covers exact match.
    return variants


# FAIR1M sub-class vocabulary (space + dash variants, lowercased).
# Note: items like "bridge" also live in _DOTA_CLASSES; they get filtered
# below by the exclusion check so FAIR1M only fires on prompts that DOTA
# DOES NOT already cover.
FAIR1M_VOCAB: frozenset[str] = frozenset({
    v for label in FAIR1M_CLASSES for v in _vocab_variants(label)
})


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _normalise(prompt: str) -> str:
    return prompt.strip().lower()


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def _prompt_hits_fair1m(prompt: str) -> bool:
    """True iff this prompt names a FAIR1M sub-class not already covered by DOTA."""
    norm = _normalise(prompt)
    if not norm:
        return False
    if norm in _DOTA_CLASSES:
        return False
    if norm in FAIR1M_VOCAB:
        return True
    # Subset match on tokens: "boeing 737" prompt hits "boeing 737" vocab,
    # "fighter jet" prompt does not hit any vocab (no FAIR1M class named
    # "fighter"; sub-classes use airframe names like "Boeing 737").
    prompt_tokens = _tokens(norm)
    if not prompt_tokens:
        return False
    for vocab_term in FAIR1M_VOCAB:
        if vocab_term in _DOTA_CLASSES:
            continue
        vocab_tokens = _tokens(vocab_term)
        if not vocab_tokens:
            continue
        # Either direction counts: prompt ⊂ vocab OR vocab ⊂ prompt.
        if vocab_tokens.issubset(prompt_tokens) or prompt_tokens.issubset(vocab_tokens):
            return True
    return False


def should_run_fair1m(
    prompts: list[str],
    *,
    force: bool = False,
) -> bool:
    """Decide whether to invoke FAIR1M-OBB for this request.

    Args:
        prompts: text prompts that will be sent to SAM3.
        force: when True (operator override via ``metadata.force_fair1m_obb``),
            bypass the gate.

    Returns:
        True if the prompts justify running FAIR1M; False to skip.
    """
    if force:
        logger.info("fair1m_gate: forced via metadata.force_fair1m_obb")
        return True
    if not prompts:
        logger.debug("fair1m_gate: skip — no prompts")
        return False
    for p in prompts:
        if _prompt_hits_fair1m(p):
            logger.info(
                "fair1m_gate: fire — prompt %r overlaps FAIR1M fine-grained vocab",
                p,
            )
            return True
    logger.debug(
        "fair1m_gate: skip — none of %d prompt(s) touch FAIR1M sub-classes",
        len(prompts),
    )
    return False


def vocab_size() -> int:
    return len(FAIR1M_VOCAB)
