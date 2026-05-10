#!/usr/bin/env python3
"""Embedding stability / re-ID test for DINOV3_SAT.

Use case: tracking a specific object (e.g. a particular vehicle) across
multiple satellite or aerial chips. The embedding layers produce per-detection
vectors that should:
  - Be similar (high cosine) for the SAME instance under different
    viewing conditions (translation, scale jitter)
  - Be dissimilar for DIFFERENT instances

Methodology
-----------
For each ground-truth bounding box in our DOTA val slice:
  1. Send the full chip with `prompt_boxes=[bbox]` to the inference API.
     SAM3 segments exactly that region, the embedding layer produces a
     vector for the crop, and the response carries the embedding.
  2. Generate K augmented bounding boxes (small translation + scale jitter
     within the original bbox neighbourhood) and repeat step 1.
  3. We now have (1 + K) embeddings for this instance.

After all instances are embedded:
  - INTRA = mean cosine similarity between embeddings of the SAME instance
  - INTER = mean cosine similarity between embeddings of DIFFERENT instances
  - SEPARATION = INTRA - INTER  (higher = better re-ID quality)

A useful embedding for tracking should show INTRA close to 1.0 and INTER
clearly below INTRA. Random embeddings would give INTRA ≈ INTER ≈ 0.

We also compute Top-1 retrieval accuracy: for each instance's primary
embedding, the nearest neighbour in the pool should be one of its own
augmented variants.

Output
------
docs/embedding_stability.md  — markdown table per layer.
docs/embedding_stability.json — raw numbers for diffing across runs.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.eval_datasets.dota import iter_dota  # noqa: E402

log = logging.getLogger("embedding_stability")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-9s %(message)s",
                    datefmt="%H:%M:%S")


# --- Embedding decoding ----------------------------------------------------

def _decode_embedding(emb: dict[str, Any]) -> np.ndarray | None:
    b64 = emb.get("fp16_b64")
    if not b64:
        return None
    try:
        return np.frombuffer(base64.b64decode(b64), dtype=np.float16).astype(np.float32)
    except Exception:
        return None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# --- Augmentation ----------------------------------------------------------

def _augment_bbox(
    bbox_xyxy: list[float],
    img_w: int,
    img_h: int,
    n_aug: int,
    rng: random.Random,
) -> list[list[float]]:
    """Generate n_aug augmented bboxes via small translation + scale jitter."""
    x1, y1, x2, y2 = bbox_xyxy
    bw, bh = x2 - x1, y2 - y1
    if bw <= 4 or bh <= 4:
        return []
    out: list[list[float]] = []
    for _ in range(n_aug):
        dx = rng.uniform(-0.10, 0.10) * bw
        dy = rng.uniform(-0.10, 0.10) * bh
        scale = rng.uniform(0.85, 1.15)
        cx = (x1 + x2) / 2.0 + dx
        cy = (y1 + y2) / 2.0 + dy
        new_w = bw * scale
        new_h = bh * scale
        nx1 = max(0, cx - new_w / 2.0)
        ny1 = max(0, cy - new_h / 2.0)
        nx2 = min(img_w, cx + new_w / 2.0)
        ny2 = min(img_h, cy + new_h / 2.0)
        if nx2 - nx1 >= 4 and ny2 - ny1 >= 4:
            out.append([nx1, ny1, nx2, ny2])
    return out


# --- Inference call --------------------------------------------------------

def _post_detect(
    url: str,
    chip_bytes: bytes,
    prompt_box_entries: list[dict[str, Any]],
    enabled_layers: list[str],
    timeout: int = 180,
) -> dict[str, Any]:
    meta = {
        "modality": "rgb",
        "prompt_boxes": prompt_box_entries,
        "enabled_layers": enabled_layers,
    }
    files = {"image": ("chip.png", chip_bytes, "image/png")}
    data = {"metadata": json.dumps(meta)}
    resp = requests.post(f"{url.rstrip('/')}/detect", files=files, data=data, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _bbox_to_prompt_entry(bbox_xyxy: list[float], img_w: int, img_h: int) -> dict[str, Any]:
    """Convert pixel xyxy bbox to the prompt_boxes entry shape sam3_runner
    expects: a dict with `bbox` = normalised [cx, cy, w, h] in [0, 1].
    """
    x1, y1, x2, y2 = (float(v) for v in bbox_xyxy)
    cx = (x1 + x2) / (2.0 * img_w)
    cy = (y1 + y2) / (2.0 * img_h)
    bw = (x2 - x1) / img_w
    bh = (y2 - y1) / img_h
    return {"bbox": [
        max(0.0, min(1.0, cx)),
        max(0.0, min(1.0, cy)),
        max(0.0, min(1.0, bw)),
        max(0.0, min(1.0, bh)),
    ]}


# --- Embedding extraction --------------------------------------------------

def _embed_bbox(
    url: str,
    chip_bytes: bytes,
    bbox_xyxy: list[float],
    img_w: int,
    img_h: int,
    enabled_layers: list[str],
) -> np.ndarray | None:
    payload = _post_detect(
        url, chip_bytes,
        [_bbox_to_prompt_entry(bbox_xyxy, img_w, img_h)],
        enabled_layers,
    )
    dets = payload.get("detections", [])
    if not dets:
        return None
    best = max(dets, key=lambda d: float(d.get("confidence", 0.0)))
    return _decode_embedding(best.get("embedding") or {})


# --- Stability evaluation --------------------------------------------------

def evaluate_stability(
    url: str,
    layer_name: str,
    chips_with_gt: list[tuple[bytes, list[dict]]],
    n_aug: int,
    max_instances: int,
    rng: random.Random,
) -> dict[str, Any]:
    enabled_layers = ["sam3", layer_name]
    log.info("Evaluating layer=%s with n_aug=%d max_instances=%d",
             layer_name, n_aug, max_instances)

    # instance_id -> list[np.ndarray] (1 original + up to n_aug augmented)
    instance_embeddings: dict[str, list[np.ndarray]] = {}
    instance_meta: dict[str, dict] = {}

    n_instance = 0
    started = time.perf_counter()
    for chip_idx, (chip_bytes, gt_boxes) in enumerate(chips_with_gt):
        if n_instance >= max_instances:
            break
        try:
            with Image.open(io.BytesIO(chip_bytes)) as img:
                img_w, img_h = img.size
        except Exception as exc:
            log.warning("chip%d: cannot open as image (%s); skipping", chip_idx, exc)
            continue

        for box_idx, gt in enumerate(gt_boxes):
            if n_instance >= max_instances:
                break
            bbox = gt.get("bbox_xyxy")
            label = gt.get("label", "?")
            if not bbox or len(bbox) != 4:
                continue
            instance_id = f"chip{chip_idx:03d}_box{box_idx:03d}"
            embeddings: list[np.ndarray] = []

            try:
                emb_orig = _embed_bbox(url, chip_bytes, bbox, img_w, img_h, enabled_layers)
            except requests.exceptions.RequestException as exc:
                log.debug("%s: original failed (%s); skipping instance", instance_id, exc)
                continue
            if emb_orig is None:
                continue
            embeddings.append(emb_orig)

            for aug_bbox in _augment_bbox(bbox, img_w, img_h, n_aug, rng):
                try:
                    emb = _embed_bbox(url, chip_bytes, aug_bbox, img_w, img_h, enabled_layers)
                except requests.exceptions.RequestException as exc:
                    log.debug("%s: augmented failed (%s); skipping aug", instance_id, exc)
                    continue
                if emb is not None:
                    embeddings.append(emb)

            if len(embeddings) >= 2:
                instance_embeddings[instance_id] = embeddings
                instance_meta[instance_id] = {"label": label, "n_emb": len(embeddings)}
                n_instance += 1
                if n_instance % 5 == 0:
                    log.info("  instances embedded: %d (elapsed %.1fs)", n_instance,
                             time.perf_counter() - started)

    if not instance_embeddings:
        return {
            "layer": layer_name,
            "error": "no successful embeddings",
            "n_instances": 0,
        }

    # --- Intra-instance similarity ---
    intra_sims: list[float] = []
    for inst_id, embs in instance_embeddings.items():
        for i in range(len(embs)):
            for j in range(i + 1, len(embs)):
                intra_sims.append(_cosine(embs[i], embs[j]))

    # --- Inter-instance similarity (pair primary embeddings) ---
    inst_ids = list(instance_embeddings.keys())
    inter_sims: list[float] = []
    for i in range(len(inst_ids)):
        for j in range(i + 1, len(inst_ids)):
            ei = instance_embeddings[inst_ids[i]][0]
            ej = instance_embeddings[inst_ids[j]][0]
            inter_sims.append(_cosine(ei, ej))

    # --- Top-1 retrieval accuracy ---
    # Build a pool of all augmented embeddings + their owning instance.
    # For each instance's PRIMARY embedding, find the nearest neighbour in
    # the pool excluding itself; check if it's another embedding from the
    # same instance.
    pool: list[tuple[str, np.ndarray]] = []
    for iid, embs in instance_embeddings.items():
        for k, e in enumerate(embs):
            pool.append((iid, e))

    correct = 0
    total = 0
    for iid, embs in instance_embeddings.items():
        primary = embs[0]
        best_score = -2.0
        best_iid: str | None = None
        for pid, pemb in pool:
            if pid == iid and pemb is embs[0]:
                continue  # skip self
            score = _cosine(primary, pemb)
            if score > best_score:
                best_score = score
                best_iid = pid
        if best_iid == iid:
            correct += 1
        total += 1
    top1_acc = correct / total if total else 0.0

    intra_arr = np.array(intra_sims) if intra_sims else np.array([0.0])
    inter_arr = np.array(inter_sims) if inter_sims else np.array([0.0])

    return {
        "layer": layer_name,
        "n_instances": len(instance_embeddings),
        "n_embeddings_total": sum(len(e) for e in instance_embeddings.values()),
        "intra_pairs": len(intra_sims),
        "inter_pairs": len(inter_sims),
        "intra_mean": float(intra_arr.mean()),
        "intra_std": float(intra_arr.std()),
        "intra_p10": float(np.percentile(intra_arr, 10)),
        "intra_p90": float(np.percentile(intra_arr, 90)),
        "inter_mean": float(inter_arr.mean()),
        "inter_std": float(inter_arr.std()),
        "inter_p10": float(np.percentile(inter_arr, 10)),
        "inter_p90": float(np.percentile(inter_arr, 90)),
        "separation": float(intra_arr.mean() - inter_arr.mean()),
        "top1_retrieval_accuracy": top1_acc,
        "elapsed_s": round(time.perf_counter() - started, 1),
    }


# --- Dataset prep ----------------------------------------------------------

def _load_dota_chips_with_gt(max_chips: int) -> list[tuple[bytes, list[dict]]]:
    out: list[tuple[bytes, list[dict]]] = []
    for chip_bytes, modality, prompts, gt in iter_dota(max_chips=max_chips):
        if modality != "rgb":
            continue
        if not gt:
            continue
        out.append((chip_bytes, gt))
    log.info("Loaded %d DOTA chips with ground truth", len(out))
    return out


# --- Report ----------------------------------------------------------------

def _build_markdown(results: list[dict[str, Any]], cfg: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Embedding Stability / Re-ID Test")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}")
    lines.append(
        f"Test corpus: DOTA-v1.0 val (max {cfg['max_instances']} instances, "
        f"{cfg['n_aug']} augmentations per instance)"
    )
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("For each ground-truth bbox we obtain (1 + N) embeddings by:")
    lines.append("  - Embedding the original bbox, AND")
    lines.append(
        "  - Embedding N augmented bboxes (translation ±10%, scale 0.85–1.15× "
        "around the original)."
    )
    lines.append("")
    lines.append(
        "**INTRA** = mean cosine similarity within the same instance "
        "(should be ≈ 1.0 for a stable embedding)."
    )
    lines.append(
        "**INTER** = mean cosine similarity between different instances "
        "(should be lower)."
    )
    lines.append("**SEPARATION** = INTRA − INTER (higher is better; useful for re-ID).")
    lines.append(
        "**Top-1 retrieval** = for each instance's primary embedding, the "
        "nearest neighbour in the embedding pool is one of its own "
        "augmented variants (vs. another instance)."
    )
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Layer | Instances | INTRA cos | INTER cos | SEPARATION | Top-1 | Eval ms |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['layer']} | — | — | — | — | — | "
                f"_{r['error']}_ |"
            )
            continue
        lines.append(
            f"| {r['layer']} | {r['n_instances']} | "
            f"{r['intra_mean']:.3f} ± {r['intra_std']:.3f} | "
            f"{r['inter_mean']:.3f} ± {r['inter_std']:.3f} | "
            f"**{r['separation']:+.3f}** | "
            f"{r['top1_retrieval_accuracy']:.1%} | "
            f"{r['elapsed_s']*1000/max(1,r['n_instances']):.0f} ms/inst |"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "- **SEPARATION ≥ 0.10**: embedding is useful for object re-ID — "
        "same-object pairs are clearly closer than different-object pairs."
    )
    lines.append(
        "- **SEPARATION ≈ 0**: embedding doesn't distinguish instances "
        "(useless for tracking)."
    )
    lines.append(
        "- **Top-1 ≥ 70%**: nearest-neighbour matching reliably identifies "
        "the same object across augmentations."
    )
    lines.append("")
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://172.18.0.2:8001")
    parser.add_argument("--max-chips", type=int, default=10,
                        help="Max DOTA chips to draw GT boxes from")
    parser.add_argument("--max-instances", type=int, default=20,
                        help="Max GT instances to embed (across all chips)")
    parser.add_argument("--n-aug", type=int, default=4,
                        help="Augmented bboxes per instance")
    parser.add_argument("--layers", default="dinov3_sat",
                        help="Comma-separated embedding layers to evaluate")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path,
                        default=REPO_ROOT / "docs" / "embedding_stability.md")
    parser.add_argument("--json-output", type=Path,
                        default=REPO_ROOT / "docs" / "embedding_stability.json")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    # Health probe
    try:
        h = requests.get(f"{args.url}/health", timeout=10)
        h.raise_for_status()
        log.info("Service reachable; SAM3_EMBED_DETECTIONS must be enabled in the container env.")
    except Exception as exc:
        log.error("Cannot reach inference service at %s: %s", args.url, exc)
        return 1

    chips = _load_dota_chips_with_gt(args.max_chips)
    if not chips:
        log.error("No DOTA chips with GT available; run scripts/fetch_real_datasets.py first")
        return 1

    layers = [l.strip() for l in args.layers.split(",") if l.strip()]
    results = []
    for layer in layers:
        try:
            r = evaluate_stability(
                args.url, layer, chips,
                n_aug=args.n_aug,
                max_instances=args.max_instances,
                rng=rng,
            )
        except Exception as exc:
            log.exception("Evaluation failed for %s", layer)
            r = {"layer": layer, "error": str(exc)}
        results.append(r)
        log.info("Done %s: %s", layer, json.dumps(r, default=str))

    cfg = {
        "max_chips": args.max_chips,
        "max_instances": args.max_instances,
        "n_aug": args.n_aug,
        "layers": layers,
        "seed": args.seed,
        "url": args.url,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_build_markdown(results, cfg), encoding="utf-8")
    args.json_output.write_text(
        json.dumps({"config": cfg, "results": results}, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("Wrote %s", args.output)
    log.info("Wrote %s", args.json_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
