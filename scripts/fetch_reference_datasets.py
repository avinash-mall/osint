#!/usr/bin/env python3
"""Fetch + stage reference imagery corpora for the Reference Embedding DB.

Multi-source orchestrator. One sub-fetcher per dataset. The build always
succeeds — adapters that can't operate (missing HF_TOKEN, missing drop-in
tarball, network unreachable) log a warning and skip. The downstream bake
iterates over whatever ended up on disk.

Layout written:
    <out>/<dataset>/<class_or_platform>/*.png
    <out>/<dataset>/MANIFEST.json    # per-chip license/attribution/sha256
    <out>/MANIFEST.sha256            # digest summary (consumed by assets entrypoint)

The on-disk layout matches what `backend/scripts/bake_reference_index.py`
expects (`--dataset-root <out>/<dataset> --license <spdx>`).

Idempotency: each adapter keys off `<out>/<dataset>/.fetched-<sha8>` where
the sha8 hashes the adapter's input manifest. Re-runs are no-ops when the
marker is current. Pair with BuildKit `--mount=type=cache` for cross-build
reuse.

Reference: docs/decisions/why-bake-reference-corpora-into-assets.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("fetch_reference_datasets")

REPO_ROOT = Path(__file__).resolve().parent.parent

# Dataset license SPDX identifiers. The bake step reads license_spdx from
# the per-chip MANIFEST entries, NOT this table — these values are only the
# adapter's default when an individual entry doesn't override.
DEFAULT_LICENSES = {
    "dota_v1":             "CC-BY-4.0",
    "dota_v2":             "CC-BY-4.0",
    "fair1m":              "CC-BY-4.0",
    "rareplanes_synth":    "CC-BY-4.0",
    "rareplanes_real":     "CC-BY-NC-4.0",
    "xview":               "CC-BY-NC-SA-4.0",   # original Maxar terms — for personal use only per operator
    "dior":                "research-only",
    "hrsc2016":            "research-only",
    "shiprsimagenet":      "research-only",
    "mvrsd":               "research-only",   # Google Earth imagery; verify redistribution
    "dvids":               "PD-USGov",
    "wikimedia":           "CC-BY-SA-4.0",
    "nara":                "PD-USGov",
    "nasa":                "PD-USGov",
}


# ---------------------------------------------------------------------------
# Per-adapter result type and runtime helpers
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """What an adapter reports back. The orchestrator aggregates these."""
    dataset: str
    status: str               # "ok" | "skipped" | "error"
    detail: str = ""
    classes_written: int = 0
    chips_written: int = 0
    manifest_path: Optional[Path] = None


@dataclass
class ChipManifestEntry:
    """One row of <dataset>/MANIFEST.json."""
    chip_path: str            # relative to <dataset>/
    class_name: str
    license_spdx: str
    attribution: str = ""
    source_url: str = ""
    sha256: str = ""
    extra: dict = field(default_factory=dict)


def _sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def _marker_path(dataset_root: Path, manifest_digest: str) -> Path:
    return dataset_root / f".fetched-{manifest_digest}"


def _is_fresh(dataset_root: Path, manifest_digest: str) -> bool:
    return _marker_path(dataset_root, manifest_digest).is_file()


def _stamp(dataset_root: Path, manifest_digest: str) -> None:
    # Remove stale markers from prior runs.
    for old in dataset_root.glob(".fetched-*"):
        try:
            old.unlink()
        except OSError:
            pass
    _marker_path(dataset_root, manifest_digest).write_text(
        json.dumps({"fetched_at": int(__import__("time").time()), "digest": manifest_digest})
    )


def _write_manifest(dataset_root: Path, dataset: str, entries: list[ChipManifestEntry]) -> Path:
    """Write <dataset>/MANIFEST.json. Returns the path."""
    out = dataset_root / "MANIFEST.json"
    payload = {
        "version": 1,
        "source_dataset": dataset,
        "chip_count": len(entries),
        "chips": [
            {
                "chip_path": e.chip_path,
                "class_name": e.class_name,
                "license_spdx": e.license_spdx,
                "attribution": e.attribution,
                "source_url": e.source_url,
                "sha256": e.sha256,
                **({"extra": e.extra} if e.extra else {}),
            }
            for e in entries
        ],
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return out


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_hf_token() -> Optional[str]:
    """Read HF_TOKEN from env or .env. Returns None if absent; adapters that
    need it MUST skip with a clear message rather than fail."""
    token = (os.environ.get("HF_TOKEN") or "").strip()
    if token:
        return token
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if line.startswith("HF_TOKEN="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    return None


def _crop_largest_bbox(img_path: Path, bbox_xyxy: tuple[int, int, int, int],
                      margin_px: int = 8, min_side: int = 8) -> Optional["Image.Image"]:
    """Borrowed from backend/scripts/stage_dota_chips.py — the canonical crop
    logic for OBB-shipping datasets. Returns None on degenerate bboxes."""
    from PIL import Image
    x1, y1, x2, y2 = bbox_xyxy
    with Image.open(img_path) as img:
        w, h = img.size
        l = max(0, int(x1) - margin_px)
        t = max(0, int(y1) - margin_px)
        r = min(w, int(x2) + margin_px)
        b = min(h, int(y2) + margin_px)
        if r - l < min_side or b - t < min_side:
            return None
        return img.crop((l, t, r, b)).copy()


# ---------------------------------------------------------------------------
# DOTA v1.0 — HuggingFace mirror, no token needed
# ---------------------------------------------------------------------------


def _fetch_dota_v1(out: Path, max_chips_per_class: int = 50) -> FetchResult:
    """Pull DOTA v1.0 val from Last-Bullet/DOTAv1.0, crop to per-class chips.

    Public HF mirror, no token required. Output layout:
        <out>/dota/<class>/<P####>__<class>.png
    """
    dataset = "dota"
    dataset_root = out / dataset
    dataset_root.mkdir(parents=True, exist_ok=True)
    license_spdx = DEFAULT_LICENSES["dota_v1"]
    manifest_digest = _sha8(f"dota_v1|{max_chips_per_class}".encode())

    if _is_fresh(dataset_root, manifest_digest):
        return FetchResult(dataset, "skipped", "marker present (idempotent re-run)")

    try:
        from huggingface_hub import HfApi, hf_hub_download
        from PIL import Image  # noqa: F401 — used in _crop_largest_bbox
    except ImportError as exc:
        return FetchResult(dataset, "error", f"missing dep: {exc}")

    repo_id = "Last-Bullet/DOTAv1.0"
    try:
        api = HfApi()
        info = api.dataset_info(repo_id)
    except Exception as exc:
        return FetchResult(dataset, "error", f"HF dataset_info failed: {exc}")

    val_imgs = sorted(
        s.rfilename for s in info.siblings
        if s.rfilename.startswith("DOTA_V1.0/val/images/")
        and s.rfilename.endswith(".png")
    )
    logger.info("dota_v1: %d val images available", len(val_imgs))

    counts: dict[str, int] = {}
    entries: list[ChipManifestEntry] = []
    chips_written = 0
    for img_rel in val_imgs:
        chip_name = Path(img_rel).stem  # P0003
        label_rel = f"DOTA_V1.0/val/labelTxt/{chip_name}.txt"
        try:
            local_img = Path(hf_hub_download(repo_id, img_rel, repo_type="dataset"))
            local_label = Path(hf_hub_download(repo_id, label_rel, repo_type="dataset"))
        except Exception as exc:
            logger.debug("dota_v1: skip %s: %s", chip_name, exc)
            continue

        # Parse DOTA labelTxt — same as fetch_real_datasets._parse_dota_label.
        for raw in local_label.read_text().splitlines():
            parts = raw.strip().split()
            if len(parts) < 9:
                continue
            try:
                coords = [float(p) for p in parts[:8]]
            except ValueError:
                continue
            cls = parts[8].strip()
            if not cls:
                continue
            xs, ys = coords[0::2], coords[1::2]
            x1, x2 = int(min(xs)), int(max(xs))
            y1, y2 = int(min(ys)), int(max(ys))
            if x2 <= x1 or y2 <= y1:
                continue

            if counts.get(cls, 0) >= max_chips_per_class:
                continue

            crop = _crop_largest_bbox(local_img, (x1, y1, x2, y2))
            if crop is None:
                continue

            class_dir = dataset_root / cls
            class_dir.mkdir(parents=True, exist_ok=True)
            chip_out = class_dir / f"{chip_name}_{counts.get(cls, 0):03d}__{cls}.png"
            crop.save(chip_out)

            entries.append(ChipManifestEntry(
                chip_path=str(chip_out.relative_to(dataset_root)),
                class_name=cls,
                license_spdx=license_spdx,
                attribution="DOTA team (Wuhan University)",
                source_url=f"https://huggingface.co/datasets/{repo_id}",
                sha256=_file_sha256(chip_out),
                extra={"bbox_xyxy": [x1, y1, x2, y2], "source_chip": chip_name},
            ))
            counts[cls] = counts.get(cls, 0) + 1
            chips_written += 1

    _write_manifest(dataset_root, dataset, entries)
    _stamp(dataset_root, manifest_digest)
    return FetchResult(
        dataset, "ok",
        detail=f"classes={len(counts)}, max_chips_per_class={max_chips_per_class}",
        classes_written=len(counts),
        chips_written=chips_written,
        manifest_path=dataset_root / "MANIFEST.json",
    )


# ---------------------------------------------------------------------------
# DOTA v2.0 — HF gated, requires HF_TOKEN
# ---------------------------------------------------------------------------


def _fetch_dota_v2(out: Path, max_chips_per_class: int = 50) -> FetchResult:
    dataset = "dota_v2"
    token = _load_hf_token()
    if not token:
        return FetchResult(dataset, "skipped", "HF_TOKEN not set (gated dataset)")
    # TODO: implement v2 fetch — same shape as v1 but different repo.
    # Common HF mirrors don't expose v2 publicly; left as adapter scaffold.
    return FetchResult(dataset, "skipped", "v2 mirror not yet wired — drop-in only")


# ---------------------------------------------------------------------------
# RarePlanes (synthetic) — HF gated
# ---------------------------------------------------------------------------


def _fetch_rareplanes(out: Path, max_chips_per_class: int = 50) -> FetchResult:
    dataset = "rareplanes_synth"
    token = _load_hf_token()
    if not token:
        return FetchResult(dataset, "skipped", "HF_TOKEN not set (gated dataset)")
    # TODO: implement once a public HF mirror is identified.
    return FetchResult(dataset, "skipped", "synthetic split not yet wired — drop-in only")


# ---------------------------------------------------------------------------
# FAIR1M — Zenodo direct
# ---------------------------------------------------------------------------


def _fetch_fair1m(out: Path, max_chips_per_class: int = 50) -> FetchResult:
    dataset = "fair1m"
    # FAIR1M is on AIRC's repo + Zenodo, but the URL changes. Left to drop-in
    # for now since the operator typically already has the val split.
    return FetchResult(dataset, "skipped", "Zenodo URL not pinned — drop-in only")


# ---------------------------------------------------------------------------
# Drop-in only datasets (no public bulk download)
# ---------------------------------------------------------------------------


def _fetch_dropin_only(dataset: str, out: Path, dropin_root: Path) -> FetchResult:
    """For datasets that only ship via account-locked portals: detect a
    pre-extracted tree under <dropin_root>/<dataset>/ and stage it as-is.

    Expected drop-in layout (one of):
        <dropin>/<dataset>/<class>/<*.png|jpg>            (already cropped)
        <dropin>/<dataset>/labels.json + <dropin>/<dataset>/chips/<*>    (DOTA-style)
    """
    src_root = dropin_root / dataset
    if not src_root.is_dir():
        return FetchResult(dataset, "skipped", f"no drop-in tree at {src_root}")

    # Content-keyed freshness so re-runs are no-ops AND a changed tree refetches.
    # Digest relative paths + sizes (cheap; no full hashing). The previous stamp
    # keyed on entry COUNT alone, so same-count content edits were missed and the
    # function re-staged + re-SHA256'd every chip on every run regardless.
    dropin_sig = "|".join(
        f"{p.relative_to(src_root).as_posix()}:{p.stat().st_size}"
        for p in sorted(src_root.rglob("*")) if p.is_file()
    )
    dropin_digest = _sha8(f"dropin|{dataset}|{dropin_sig}".encode())

    dataset_root = out / dataset
    if _is_fresh(dataset_root, dropin_digest):
        return FetchResult(dataset, "ok", detail=f"drop-in {src_root} unchanged — cached")
    dataset_root.mkdir(parents=True, exist_ok=True)
    license_spdx = DEFAULT_LICENSES.get(dataset, "see-source-terms")

    # Heuristic: if labels.json present, run the DOTA stage logic. Otherwise
    # assume per-class subdirectories of cropped chips and just rsync.
    if (src_root / "labels.json").is_file():
        # Reuse the existing staging helper.
        try:
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            return FetchResult(dataset, "error", f"PIL missing: {exc}")
        # Lazy-import to avoid sys.path tangle when run from /build.
        stage_script = REPO_ROOT / "backend" / "scripts" / "stage_dota_chips.py"
        if stage_script.is_file():
            sys.path.insert(0, str(stage_script.parent))
            try:
                from stage_dota_chips import stage  # type: ignore
            finally:
                sys.path.pop(0)
            try:
                counts = stage(
                    labels_json=src_root / "labels.json",
                    chips_dir=src_root,
                    out_root=dataset_root,
                )
            except RuntimeError as exc:
                return FetchResult(dataset, "error", f"stage failed: {exc}")
        else:
            return FetchResult(dataset, "error", "stage_dota_chips.py not reachable")
    else:
        # Per-class subdir layout. rsync into dataset_root.
        for class_dir in src_root.iterdir():
            if not class_dir.is_dir():
                continue
            out_class = dataset_root / class_dir.name
            out_class.mkdir(parents=True, exist_ok=True)
            for chip in class_dir.iterdir():
                if chip.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                    continue
                target = out_class / chip.name
                if not target.exists():
                    shutil.copy2(chip, target)
        counts = {c.name: sum(1 for _ in c.iterdir())
                  for c in dataset_root.iterdir() if c.is_dir()}

    # Build the per-chip manifest from the staged tree.
    entries: list[ChipManifestEntry] = []
    for class_dir in dataset_root.iterdir():
        if not class_dir.is_dir():
            continue
        for chip in class_dir.iterdir():
            if chip.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            entries.append(ChipManifestEntry(
                chip_path=str(chip.relative_to(dataset_root)),
                class_name=class_dir.name,
                license_spdx=license_spdx,
                attribution=f"{dataset} (operator-provided drop-in)",
                source_url=f"file://{src_root}",
                sha256=_file_sha256(chip),
            ))
    _write_manifest(dataset_root, dataset, entries)
    _stamp(dataset_root, dropin_digest)
    return FetchResult(
        dataset, "ok",
        detail=f"drop-in {src_root} → {len(entries)} chips",
        classes_written=len(counts) if isinstance(counts, dict) else 0,
        chips_written=len(entries),
        manifest_path=dataset_root / "MANIFEST.json",
    )


# ---------------------------------------------------------------------------
# MVRSD — drop-in only (Baidu/SciDB account-locked), YOLO/VOC bbox source
# ---------------------------------------------------------------------------

# MVRSD class-index → name. The label indices follow MVRSD's classes.txt order
# (NOT the community repo's data.yaml `names`, which is inconsistent with its
# own indices). See scripts/manifests/mvrsd.json.
_MVRSD_CLASSES = ["SMV", "LMV", "AFV", "CV", "MCV"]


def _mvrsd_yolo_to_xyxy(line: str, img_w: int, img_h: int) -> Optional[tuple[str, tuple[int, int, int, int]]]:
    parts = line.split()
    if len(parts) < 5:
        return None
    try:
        idx = int(float(parts[0]))
        cx, cy, bw, bh = (float(p) for p in parts[1:5])
    except ValueError:
        return None
    if idx < 0 or idx >= len(_MVRSD_CLASSES):
        return None
    x1 = int((cx - bw / 2.0) * img_w)
    y1 = int((cy - bh / 2.0) * img_h)
    x2 = int((cx + bw / 2.0) * img_w)
    y2 = int((cy + bh / 2.0) * img_h)
    if x2 <= x1 or y2 <= y1:
        return None
    return _MVRSD_CLASSES[idx], (x1, y1, x2, y2)


def _fetch_mvrsd(out: Path, dropin_root: Path) -> FetchResult:
    """Crop per-class reference chips from an operator-supplied MVRSD tree.

    MVRSD imagery is account-locked (Baidu Cloud / SciDB), so this is a drop-in
    adapter: it skips cleanly when no tree is present. The drop-in ships YOLO
    bbox labels (not pre-cropped per-class chips), so — unlike the generic
    ``_fetch_dropin_only`` — we crop the *largest* labelled bbox per image into
    ``<out>/mvrsd/<class>/<stem>__<class>.png`` so the bake can iterate it.

    Expected drop-in layout:
        <dropin>/mvrsd/images/{train,val}/<stem>.jpg
        <dropin>/mvrsd/labels/{train,val}/<stem>.txt   (YOLO)
    """
    dataset = "mvrsd"
    src_root = dropin_root / dataset
    img_root = src_root / "images"
    lbl_root = src_root / "labels"
    if not img_root.is_dir():
        return FetchResult(dataset, "skipped", f"no drop-in tree at {img_root}")

    try:
        from PIL import Image
    except ImportError as exc:
        return FetchResult(dataset, "error", f"PIL missing: {exc}")

    # Content-keyed freshness (relative path + size), same pattern as drop-in.
    sig = "|".join(
        f"{p.relative_to(src_root).as_posix()}:{p.stat().st_size}"
        for p in sorted(src_root.rglob("*")) if p.is_file()
    )
    digest = _sha8(f"mvrsd|{sig}".encode())
    dataset_root = out / dataset
    if _is_fresh(dataset_root, digest):
        return FetchResult(dataset, "ok", detail=f"drop-in {src_root} unchanged — cached")
    dataset_root.mkdir(parents=True, exist_ok=True)
    license_spdx = DEFAULT_LICENSES.get(dataset, "research-only")

    entries: list[ChipManifestEntry] = []
    counts: dict[str, int] = {}
    for split in ("train", "val"):
        split_img = img_root / split
        split_lbl = lbl_root / split
        if not split_img.is_dir():
            continue
        for img_path in sorted(split_img.iterdir()):
            if img_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            lbl_path = split_lbl / f"{img_path.stem}.txt"
            if not lbl_path.is_file():
                continue
            with Image.open(img_path) as im:
                w, h = im.size
            # Pick the largest-area labelled box as the chip's class assignment.
            best: Optional[tuple[str, tuple[int, int, int, int], int]] = None
            for raw in lbl_path.read_text().splitlines():
                parsed = _mvrsd_yolo_to_xyxy(raw, w, h)
                if parsed is None:
                    continue
                cls, (x1, y1, x2, y2) = parsed
                area = (x2 - x1) * (y2 - y1)
                if best is None or area > best[2]:
                    best = (cls, (x1, y1, x2, y2), area)
            if best is None:
                continue
            cls, bbox, _ = best
            crop = _crop_largest_bbox(img_path, bbox)
            if crop is None:
                continue
            class_dir = dataset_root / cls
            class_dir.mkdir(parents=True, exist_ok=True)
            chip_out = class_dir / f"{img_path.stem}__{cls}.png"
            crop.save(chip_out)
            entries.append(ChipManifestEntry(
                chip_path=str(chip_out.relative_to(dataset_root)),
                class_name=cls,
                license_spdx=license_spdx,
                attribution="MVRSD (Google Earth; baidongls/MVRSD) — operator-provided drop-in",
                source_url="https://github.com/baidongls/MVRSD",
                sha256=_file_sha256(chip_out),
                extra={"bbox_xyxy": list(bbox), "split": split},
            ))
            counts[cls] = counts.get(cls, 0) + 1

    _write_manifest(dataset_root, dataset, entries)
    _stamp(dataset_root, digest)
    return FetchResult(
        dataset, "ok",
        detail=f"drop-in {src_root} → {len(entries)} chips across {len(counts)} classes",
        classes_written=len(counts),
        chips_written=len(entries),
        manifest_path=dataset_root / "MANIFEST.json",
    )


# ---------------------------------------------------------------------------
# Manifest-driven HTTP fetchers (Wikimedia, NARA, NASA, DVIDS)
# ---------------------------------------------------------------------------


def _fetch_from_manifest(out: Path, manifest_path: Path) -> FetchResult:
    """Read a curated <source>.json manifest and download each item.

    Manifest schema:
        {
          "source_dataset": "wikimedia",
          "default_license_spdx": "CC-BY-SA-4.0",
          "platforms": [
            {
              "platform_name": "F-16 Fighting Falcon",
              "view_domain": "ground",
              "items": [
                {"url": "...", "sha256": "...", "attribution": "...",
                 "license_spdx": "PD-USGov"  # per-item override
                }
              ]
            }
          ]
        }
    """
    if not manifest_path.is_file():
        return FetchResult(manifest_path.stem, "skipped", f"manifest {manifest_path} missing")

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        return FetchResult(manifest_path.stem, "error", f"manifest parse: {exc}")

    dataset = manifest.get("source_dataset") or manifest_path.stem
    default_license = manifest.get("default_license_spdx") or DEFAULT_LICENSES.get(dataset, "")
    dataset_root = out / dataset
    dataset_root.mkdir(parents=True, exist_ok=True)

    digest_input = json.dumps(manifest, sort_keys=True).encode()
    manifest_digest = _sha8(digest_input)
    if _is_fresh(dataset_root, manifest_digest):
        return FetchResult(dataset, "skipped", "marker matches manifest digest")

    try:
        import urllib.request
    except ImportError as exc:
        return FetchResult(dataset, "error", f"urllib missing: {exc}")

    entries: list[ChipManifestEntry] = []
    classes_seen: set[str] = set()
    chips_written = 0
    for platform in manifest.get("platforms", []):
        platform_name = platform.get("platform_name") or ""
        if not platform_name:
            continue
        # Use the platform_name as the class subdir so the bake can iterate it.
        slug = platform_name.replace(" ", "-").replace("/", "-")
        class_dir = dataset_root / slug
        class_dir.mkdir(parents=True, exist_ok=True)
        classes_seen.add(slug)

        for idx, item in enumerate(platform.get("items", [])):
            url = item.get("url")
            if not url:
                continue
            expected_sha = (item.get("sha256") or "").lower()
            ext = Path(url.split("?", 1)[0]).suffix.lower() or ".jpg"
            if ext not in {".png", ".jpg", ".jpeg"}:
                ext = ".jpg"
            chip_out = class_dir / f"{slug}_{idx:03d}{ext}"

            if not chip_out.exists():
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "sentinel-reference-bake/1.0"}
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        chip_out.write_bytes(resp.read())
                except Exception as exc:
                    logger.warning("%s: failed %s: %s", dataset, url, exc)
                    continue

            actual_sha = _file_sha256(chip_out)
            if expected_sha and actual_sha != expected_sha:
                logger.warning(
                    "%s: sha256 mismatch for %s (got %s, expected %s) — keeping anyway",
                    dataset, chip_out.name, actual_sha[:12], expected_sha[:12],
                )

            entries.append(ChipManifestEntry(
                chip_path=str(chip_out.relative_to(dataset_root)),
                class_name=slug,
                license_spdx=item.get("license_spdx") or default_license or "unknown",
                attribution=item.get("attribution") or "",
                source_url=url,
                sha256=actual_sha,
                extra={"view_domain": platform.get("view_domain", "ground"),
                       "platform_name": platform_name},
            ))
            chips_written += 1

    _write_manifest(dataset_root, dataset, entries)
    _stamp(dataset_root, manifest_digest)
    return FetchResult(
        dataset, "ok",
        detail=f"manifest {manifest_path.name} → {chips_written} chips across {len(classes_seen)} platforms",
        classes_written=len(classes_seen),
        chips_written=chips_written,
        manifest_path=dataset_root / "MANIFEST.json",
    )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def _summary_digest(out: Path) -> str:
    """SHA-256 over every per-dataset MANIFEST.json. Written to MANIFEST.sha256
    so the assets entrypoint can detect "image baked new corpora; resync volume"."""
    h = hashlib.sha256()
    for manifest in sorted(out.glob("*/MANIFEST.json")):
        h.update(manifest.relative_to(out).as_posix().encode())
        h.update(b"\0")
        h.update(manifest.read_bytes())
    return h.hexdigest()


def run(
    out: Path,
    dropin_root: Path,
    manifests_root: Path,
    max_chips_per_class: int = 50,
    only: Optional[set[str]] = None,
) -> list[FetchResult]:
    """Run every adapter. Returns the per-dataset result list."""
    out.mkdir(parents=True, exist_ok=True)
    results: list[FetchResult] = []

    def _gate(name: str) -> bool:
        return only is None or name in only

    if _gate("dota"):
        results.append(_fetch_dota_v1(out, max_chips_per_class))
    if _gate("dota_v2"):
        results.append(_fetch_dota_v2(out, max_chips_per_class))
    if _gate("rareplanes_synth"):
        results.append(_fetch_rareplanes(out, max_chips_per_class))
    if _gate("fair1m"):
        results.append(_fetch_fair1m(out, max_chips_per_class))

    # Drop-in only datasets — gated on a tarball under ./reference-corpora-input/<dataset>/
    for ds in ("xview", "dior", "hrsc2016", "shiprsimagenet"):
        if _gate(ds):
            results.append(_fetch_dropin_only(ds, out, dropin_root))

    # MVRSD — drop-in only too, but ships YOLO/VOC bbox labels (not pre-cropped
    # per-class chips), so it gets a dedicated bbox-cropping adapter.
    if _gate("mvrsd"):
        results.append(_fetch_mvrsd(out, dropin_root))

    # Manifest-driven (Wikimedia, NARA, NASA, DVIDS)
    for ds in ("wikimedia", "nara", "nasa", "dvids"):
        if _gate(ds):
            mf = manifests_root / f"{ds}.json"
            results.append(_fetch_from_manifest(out, mf))

    # Emit summary digest.
    digest = _summary_digest(out)
    (out / "MANIFEST.sha256").write_text(digest + "\n")
    logger.info("MANIFEST.sha256: %s", digest)
    return results


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Fetch reference imagery corpora into a chip tree")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output root (e.g. /build/reference-chips)")
    ap.add_argument("--dropin", type=Path, default=REPO_ROOT / "reference-corpora-input",
                    help="Drop-in tarball root for restricted-access datasets")
    ap.add_argument("--manifests", type=Path, default=REPO_ROOT / "scripts" / "manifests",
                    help="Directory containing wikimedia.json, nara.json, nasa.json, dvids.json")
    ap.add_argument("--max-chips-per-class", type=int, default=50)
    ap.add_argument("--only", action="append",
                    help="Limit to one or more datasets (repeatable)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    only = set(args.only) if args.only else None
    results = run(
        out=args.out,
        dropin_root=args.dropin,
        manifests_root=args.manifests,
        max_chips_per_class=args.max_chips_per_class,
        only=only,
    )
    # Stdout JSON summary (build script reads this).
    summary = {
        "datasets": [
            {
                "dataset": r.dataset,
                "status": r.status,
                "detail": r.detail,
                "classes_written": r.classes_written,
                "chips_written": r.chips_written,
            }
            for r in results
        ],
        "totals": {
            "datasets_ok": sum(1 for r in results if r.status == "ok"),
            "datasets_skipped": sum(1 for r in results if r.status == "skipped"),
            "datasets_error": sum(1 for r in results if r.status == "error"),
            "chips_total": sum(r.chips_written for r in results),
        },
    }
    print(json.dumps(summary, indent=2))
    # Build must NOT fail just because one optional adapter erred — exit 0
    # unless this run truly has no usable corpora on disk. A BuildKit cache
    # mount carrying up-to-date markers from a prior successful bake is the
    # common case where chips_written totals 0 but the chip tree under
    # ``args.out`` is fully populated; the next stage's `cp -an` will then
    # copy that cache content out. Only treat zero new chips AND zero
    # on-disk chips as a real misconfig.
    if summary["totals"]["chips_total"] == 0:
        on_disk_chips = sum(1 for _ in args.out.rglob("*.png")) \
                      + sum(1 for _ in args.out.rglob("*.jpg")) \
                      + sum(1 for _ in args.out.rglob("*.jpeg"))
        if on_disk_chips == 0:
            logger.error(
                "fetch_reference_datasets produced 0 chips and the output tree at %s is empty — "
                "build aborted (no usable corpora; check HF_TOKEN, drop-in trees, or network)",
                args.out,
            )
            return 1
        logger.warning(
            "0 chips written this run, but %d chip files already exist in %s "
            "(BuildKit cache from a prior bake) — proceeding",
            on_disk_chips, args.out,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
