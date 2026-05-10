#!/usr/bin/env python3
"""DINOV3_SAT embedding stability across video frames (object re-ID test).

DINOV3_LVD was removed from the codebase after this script proved it produces
NaN embeddings on real drone-video crops while DINOV3_SAT works correctly
(SEP=+0.217 on 1440p drone footage). See docs/video_tracking_stability.md.

The inference service's /detect_video endpoint has SDK dependency issues
(missing flash_attn_interface, init_state kwarg mismatches). Instead, we
extract frames locally with cv2 and call /detect on each, then chain
detections across consecutive frames by bbox IoU to synthesize tracks.

For each synthesized track (≥ 2 frames where the same object appears):
  - INTRA = mean cosine similarity between embeddings of the same track
  - INTER = mean cosine similarity between embeddings of different tracks
  - SEPARATION = INTRA - INTER (higher = better re-ID quality)
  - Top-1 retrieval = nearest neighbour points to a same-track frame

This evaluates whether DINOV3_SAT or DINOV3_LVD embeddings can be used to
re-identify the same object across video frames — the actual question users
care about for "tracking a specific car across drone footage".

Output: docs/video_tracking_stability.md + .json
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("video_tracking_stability")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-9s %(message)s",
                    datefmt="%H:%M:%S")


# ---------------------------------------------------------------------------
# Embedding decode + cosine
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# IoU for chaining detections across frames
# ---------------------------------------------------------------------------

def _iou(a_xyxy: list[float], b_xyxy: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a_xyxy
    bx1, by1, bx2, by2 = b_xyxy
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

def extract_frames(video_path: Path, n_frames: int) -> list[tuple[int, bytes, int, int]]:
    """Extract n_frames evenly-spaced frames from the video.
    Returns list of (frame_idx, png_bytes, width, height).
    """
    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if total == 0:
        cap.release()
        return []
    step = max(1, total // n_frames)
    out = []
    for k in range(n_frames):
        idx = min(total - 1, k * step)
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append((idx, buf.getvalue(), fw, fh))
    cap.release()
    return out


# ---------------------------------------------------------------------------
# /detect call
# ---------------------------------------------------------------------------

def detect_with_embedding(
    url: str,
    chip_bytes: bytes,
    prompts: list[str],
    embed_layer: str,
    img_w: int,
    img_h: int,
    timeout: int = 120,
) -> list[dict] | None:
    """POST a frame to /detect with a single embedding layer enabled. Returns
    list of {bbox_xyxy_pixel, label, score, embedding} on success.
    """
    meta = {
        "modality": "rgb",
        "text_prompts": prompts,
        "max_prompts": len(prompts),
        "enabled_layers": ["sam3", embed_layer],
    }
    try:
        r = requests.post(
            f"{url.rstrip('/')}/detect",
            files={"image": ("frame.png", chip_bytes, "image/png")},
            data={"metadata": json.dumps(meta)},
            timeout=timeout,
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as exc:
        log.warning("frame detect failed: %s", exc)
        return None
    data = r.json()
    out = []
    for det in data.get("detections", []):
        # bbox is normalized [cx, cy, w, h]
        b = det.get("bbox")
        if not isinstance(b, list) or len(b) != 4:
            continue
        cx, cy, bw, bh = b
        x1 = (cx - bw / 2.0) * img_w
        y1 = (cy - bh / 2.0) * img_h
        x2 = (cx + bw / 2.0) * img_w
        y2 = (cy + bh / 2.0) * img_h
        emb = _decode_embedding(det.get("embedding") or {})
        if emb is None:
            continue
        out.append({
            "bbox_xyxy_pixel": [x1, y1, x2, y2],
            "label": det.get("class") or det.get("label") or "?",
            "score": float(det.get("confidence", 0.0)),
            "embedding": emb,
        })
    return out


# ---------------------------------------------------------------------------
# IoU-based track chaining across frames
# ---------------------------------------------------------------------------

def chain_tracks(
    frames: list[list[dict]],
    iou_threshold: float = 0.3,
) -> list[list[dict]]:
    """Greedy chaining: for each detection in frame N, find the highest-IoU
    unmatched detection in frame N+1 above iou_threshold and assign to the
    same track.

    Returns list of tracks; each track is a list of {frame_idx, det_idx,
    bbox_xyxy_pixel, embedding, label, score}.
    """
    if not frames:
        return []
    # Each detection initially gets its own track.
    next_tid = 0
    # detection index -> track id (per frame)
    track_assign: list[dict[int, int]] = [{} for _ in frames]
    # track_id -> ordered list of detection refs
    tracks: dict[int, list[dict]] = {}

    for di, det in enumerate(frames[0]):
        track_assign[0][di] = next_tid
        tracks[next_tid] = [{**det, "frame_idx": 0, "det_idx": di}]
        next_tid += 1

    for fi in range(1, len(frames)):
        cur_dets = frames[fi]
        prev_dets = frames[fi - 1]
        used_prev = set()
        for di, det in enumerate(cur_dets):
            best_score = iou_threshold
            best_pi = None
            for pi, pdet in enumerate(prev_dets):
                if pi in used_prev:
                    continue
                score = _iou(det["bbox_xyxy_pixel"], pdet["bbox_xyxy_pixel"])
                if score > best_score:
                    best_score = score
                    best_pi = pi
            if best_pi is not None:
                tid = track_assign[fi - 1].get(best_pi)
                if tid is not None:
                    track_assign[fi][di] = tid
                    tracks[tid].append({**det, "frame_idx": fi, "det_idx": di})
                    used_prev.add(best_pi)
                    continue
            # New track
            track_assign[fi][di] = next_tid
            tracks[next_tid] = [{**det, "frame_idx": fi, "det_idx": di}]
            next_tid += 1

    return list(tracks.values())


# ---------------------------------------------------------------------------
# Stability metric
# ---------------------------------------------------------------------------

def compute_stability(tracks: list[list[dict]]) -> dict[str, Any]:
    # Only keep tracks with ≥ 2 frames.
    multi = [t for t in tracks if len(t) >= 2]

    intra_sims: list[float] = []
    for tr in multi:
        for i in range(len(tr)):
            for j in range(i + 1, len(tr)):
                intra_sims.append(_cosine(tr[i]["embedding"], tr[j]["embedding"]))

    inter_sims: list[float] = []
    for i in range(len(multi)):
        for j in range(i + 1, len(multi)):
            inter_sims.append(_cosine(multi[i][0]["embedding"], multi[j][0]["embedding"]))

    # Top-1 retrieval
    pool: list[tuple[int, np.ndarray]] = []
    for ti, tr in enumerate(multi):
        for det in tr:
            pool.append((ti, det["embedding"]))
    correct, total = 0, 0
    for ti, tr in enumerate(multi):
        primary = tr[0]["embedding"]
        best_score, best_ti = -2.0, None
        for pti, pemb in pool:
            if pti == ti and pemb is tr[0]["embedding"]:
                continue
            score = _cosine(primary, pemb)
            if score > best_score:
                best_score, best_ti = score, pti
        if best_ti == ti:
            correct += 1
        total += 1
    top1 = correct / total if total else 0.0

    intra = np.array(intra_sims) if intra_sims else np.array([0.0])
    inter = np.array(inter_sims) if inter_sims else np.array([0.0])
    return {
        "n_tracks_multi_frame": len(multi),
        "n_tracks_total": len(tracks),
        "intra_pairs": len(intra_sims),
        "inter_pairs": len(inter_sims),
        "intra_mean": float(intra.mean()),
        "intra_std": float(intra.std()),
        "inter_mean": float(inter.mean()),
        "inter_std": float(inter.std()),
        "separation": float(intra.mean() - inter.mean()),
        "top1_retrieval_accuracy": top1,
    }


# ---------------------------------------------------------------------------
# Per-video, per-layer evaluation
# ---------------------------------------------------------------------------

def evaluate_video(
    url: str,
    video_path: Path,
    prompts: list[str],
    embed_layer: str,
    n_frames: int,
    iou_threshold: float,
) -> dict[str, Any]:
    log.info("=== %s | embed_layer=%s ===", video_path.name, embed_layer)
    started = time.perf_counter()

    raw_frames = extract_frames(video_path, n_frames)
    if not raw_frames:
        return {"video": video_path.name, "embed_layer": embed_layer, "error": "no frames extracted"}
    log.info("Extracted %d frames", len(raw_frames))

    frames_dets: list[list[dict]] = []
    for frame_idx, fb, fw, fh in raw_frames:
        dets = detect_with_embedding(url, fb, prompts, embed_layer, fw, fh)
        if dets is None:
            dets = []
        log.info("  frame %d: %d detections", frame_idx, len(dets))
        frames_dets.append(dets)

    tracks = chain_tracks(frames_dets, iou_threshold=iou_threshold)
    multi_frame = [t for t in tracks if len(t) >= 2]
    log.info(
        "Chained into %d tracks (%d multi-frame, IoU≥%.2f)",
        len(tracks), len(multi_frame), iou_threshold,
    )
    if len(multi_frame) < 2:
        return {
            "video": video_path.name,
            "embed_layer": embed_layer,
            "error": f"only {len(multi_frame)} multi-frame tracks (need ≥ 2)",
            "n_tracks_total": len(tracks),
        }

    metrics = compute_stability(tracks)
    metrics.update({
        "video": video_path.name,
        "embed_layer": embed_layer,
        "n_frames_processed": len(raw_frames),
        "iou_threshold": iou_threshold,
        "elapsed_s": round(time.perf_counter() - started, 1),
    })
    log.info("metrics: %s", json.dumps({k: v for k, v in metrics.items()
                                          if k not in ("video", "embed_layer")}, default=str))
    return metrics


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_markdown(results: list[dict], cfg: dict) -> str:
    lines = []
    lines.append("# Video Tracking — Embedding Stability")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        f"For each drone video, extract {cfg['n_frames']} evenly-spaced frames with "
        "cv2; POST each frame to `/detect` with `enabled_layers=['sam3', "
        "'<EMBED_LAYER>']`; collect per-detection embeddings + bboxes."
    )
    lines.append("")
    lines.append(
        f"Synthesise tracks by chaining detections across consecutive frames "
        f"with bbox IoU ≥ {cfg['iou_threshold']} (greedy match). For each "
        "multi-frame track:"
    )
    lines.append(
        "- **INTRA** = mean cosine similarity within the track (close to 1 = "
        "embedding is stable across frames)."
    )
    lines.append(
        "- **INTER** = mean cosine similarity between tracks (lower = "
        "embeddings discriminate instances)."
    )
    lines.append(
        "- **SEPARATION** = INTRA − INTER (higher is better; target ≥ 0.10 "
        "for useful re-ID)."
    )
    lines.append(
        "- **Top-1 retrieval** = each track's primary embedding's nearest "
        "neighbour in the pool (excluding itself) is from the same track."
    )
    lines.append("")
    lines.append(
        "*This sidesteps `/detect_video`, which currently has SDK dependency "
        "issues (missing `flash_attn_interface`, multiplex `init_state` kwarg "
        "mismatches). The cv2-extracted-frames approach gives the same "
        "tracking-quality signal.*"
    )
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append(
        "| Video | Embed Layer | Tracks (multi-frame / total) | INTRA | INTER | "
        "**SEPARATION** | **Top-1** | Eval s |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['video']} | {r['embed_layer']} | — | — | — | — | — | "
                f"_{r['error']}_ |"
            )
            continue
        lines.append(
            f"| {r['video']} | {r['embed_layer']} | "
            f"{r['n_tracks_multi_frame']} / {r['n_tracks_total']} | "
            f"{r['intra_mean']:.3f} ± {r['intra_std']:.3f} | "
            f"{r['inter_mean']:.3f} ± {r['inter_std']:.3f} | "
            f"**{r['separation']:+.3f}** | "
            f"**{r['top1_retrieval_accuracy']:.1%}** | "
            f"{r['elapsed_s']:.1f} |"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "- **SEPARATION ≥ 0.10**: embedding usefully distinguishes tracked "
        "objects across frames. Useful for re-ID workflows."
    )
    lines.append(
        "- **Top-1 ≥ 70%**: nearest-neighbour matching reliably finds "
        "another frame of the same object."
    )
    lines.append(
        "- **DINOV3_SAT** is satellite-tuned (sat493m pretraining); **DINOV3_LVD** "
        "is FMV/video-tuned (lvd1689m). For drone-video tracking, the better "
        "performer should be preferred — see the SEPARATION column."
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://172.18.0.2:8001")
    parser.add_argument("--videos",
                        default="sample/53902-476396222_medium.mp4,sample/168811-839864556_medium.mp4")
    parser.add_argument("--prompts", default="car,vehicle,person,truck")
    parser.add_argument("--n-frames", type=int, default=8,
                        help="Frames to extract per video (evenly spaced)")
    parser.add_argument("--iou-threshold", type=float, default=0.3,
                        help="Minimum IoU between consecutive frames to chain detections")
    parser.add_argument("--layers", default="dinov3_sat")
    parser.add_argument("--output", type=Path,
                        default=REPO_ROOT / "docs" / "video_tracking_stability.md")
    parser.add_argument("--json-output", type=Path,
                        default=REPO_ROOT / "docs" / "video_tracking_stability.json")
    args = parser.parse_args()

    try:
        h = requests.get(f"{args.url}/health", timeout=10)
        h.raise_for_status()
    except Exception as exc:
        log.error("Cannot reach inference service at %s: %s", args.url, exc)
        return 1

    videos = []
    for v in args.videos.split(","):
        v = v.strip()
        if not v:
            continue
        p = Path(v)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists():
            log.warning("Video not found: %s", p)
            continue
        videos.append(p)

    if not videos:
        log.error("No videos found")
        return 1

    layers = [l.strip() for l in args.layers.split(",") if l.strip()]
    prompts = [p.strip() for p in args.prompts.split(",") if p.strip()]

    results = []
    for video_path in videos:
        for layer in layers:
            try:
                r = evaluate_video(
                    args.url, video_path, prompts, layer,
                    args.n_frames, args.iou_threshold,
                )
            except Exception as exc:
                log.exception("Evaluation failed for %s/%s", video_path.name, layer)
                r = {"video": video_path.name, "embed_layer": layer, "error": str(exc)}
            results.append(r)

    cfg = {
        "url": args.url,
        "videos": [str(v) for v in videos],
        "prompts": prompts,
        "n_frames": args.n_frames,
        "iou_threshold": args.iou_threshold,
        "layers": layers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_markdown(results, cfg), encoding="utf-8")
    args.json_output.write_text(
        json.dumps({"config": cfg, "results": results}, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("Wrote %s", args.output)
    log.info("Wrote %s", args.json_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
