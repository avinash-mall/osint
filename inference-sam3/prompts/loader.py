from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROMPTS_DIR = Path(__file__).parent
EXTRA_FILE = os.getenv("SAM3_LABEL_FILE", "").strip()
FORCED_PROFILE = os.getenv("SAM3_DEFAULT_PROMPT_PROFILE", "").strip()


def _normalize(label: str) -> str:
    return " ".join(label.strip().lower().split())


def _load_profile(name: str) -> list[str]:
    path = PROMPTS_DIR / f"{name}.json"
    if not path.exists():
        raise ValueError(f"Unknown prompt profile {name!r}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    prompts = payload.get("prompts")
    if not isinstance(prompts, list):
        raise ValueError(f"Prompt profile {name!r} does not contain a prompts list")
    return [str(item) for item in prompts]


def select_default_profile(modality: str) -> str:
    return "ground_v1" if (modality or "").lower() == "fmv" else "satellite_v1"


def _load_extra_file(path: str) -> list[str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [str(item) for item in payload]
    prompts = payload.get("prompts") if isinstance(payload, dict) else None
    if not isinstance(prompts, list):
        raise ValueError(f"Prompt override file {path!r} does not contain prompts")
    return [str(item) for item in prompts]


def resolve_prompts(meta: dict[str, Any] | None, *, max_prompts: int) -> list[str]:
    meta = meta or {}
    if isinstance(meta.get("text_prompts"), list) and meta["text_prompts"]:
        prompts = [str(item) for item in meta["text_prompts"]]
    elif EXTRA_FILE and Path(EXTRA_FILE).exists():
        prompts = _load_extra_file(EXTRA_FILE)
    else:
        modality = str(meta.get("modality") or "rgb").lower()
        profile = str(meta.get("prompt_profile") or FORCED_PROFILE or select_default_profile(modality))
        prompts = _load_profile(profile)

    seen: set[str] = set()
    out: list[str] = []
    for raw in prompts:
        normalized = _normalize(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= max(1, max_prompts):
            break
    if not out:
        raise ValueError("No labels supplied for SAM3")
    return out
