#!/usr/bin/env python3
"""Repair copied YOLO dataset metadata after moving a training run between hosts.

This utility is intentionally conservative: it can recreate policy metadata and
manifest-level reports, but it will not pretend that an old source-label dataset
has been rebuilt with the new optical-defense taxonomy.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from detection_policy import (
    PARENT_CLASSES as DEFENSE_PARENT_CLASSES,   # legacy alias — open-vocab clusters
    TAXONOMY_VERSION,
    active_detection_policy,
    parent_class_for_label,
)

# Open-vocabulary policy no longer has a fixed distractor list. Kept as an
# empty tuple so legacy training-side checks in this script remain syntactically
# valid; nothing is suppressed at runtime.
DISTRACTOR_PARENT_CLASSES: tuple[str, ...] = ()


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YOLO_ROOT = REPO_ROOT / "training_dataset" / "yolo"


def read_classes(yolo_root: Path) -> list[str]:
    classes_path = yolo_root / "classes.json"
    if classes_path.exists():
        data = json.loads(classes_path.read_text(encoding="utf-8"))
        names = data.get("names")
        if isinstance(names, list):
            return [str(name) for name in names]

    data_yaml = yolo_root / "data.yaml"
    if not data_yaml.exists():
        raise SystemExit(f"No classes.json or data.yaml found under {yolo_root}")

    names: list[str] = []
    in_names = False
    for line in data_yaml.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "names:":
            in_names = True
            continue
        if in_names and ":" in stripped:
            _key, value = stripped.split(":", 1)
            names.append(value.strip().strip("'\""))
    if not names:
        raise SystemExit(f"No class names found in {data_yaml}")
    return names


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fix_data_yaml_path(yolo_root: Path) -> bool:
    path = yolo_root / "data.yaml"
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    next_lines = []
    changed = False
    local_path = yolo_root.resolve().as_posix()
    for line in lines:
        if line.startswith("path: "):
            next_line = f"path: {local_path}"
            changed = changed or next_line != line
            next_lines.append(next_line)
        else:
            next_lines.append(line)
    if changed:
        path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    return changed


def manifest_reports(yolo_root: Path) -> dict[str, Any]:
    manifest = yolo_root / "manifest.jsonl"
    if not manifest.exists():
        return {"manifest_found": False}
    summary_path = yolo_root / "summary.json"
    expected_tiles = None
    summary = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        datasets = summary.get("datasets") if isinstance(summary, dict) else None
        if isinstance(datasets, list):
            expected_tiles = sum(int(item.get("tiles_written") or 0) for item in datasets)

    split_counts: dict[str, Counter] = defaultdict(Counter)
    source_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    rows_seen = 0
    with manifest.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows_seen += 1
            item = json.loads(line)
            split = str(item.get("split") or "unknown")
            dataset = str(item.get("dataset") or "unknown")
            labels = int(item.get("labels") or 0)
            split_counts[split]["tiles"] += 1
            split_counts[split]["labels"] += labels
            source_counts[(split, dataset)]["tiles"] += 1
            source_counts[(split, dataset)]["labels"] += labels
            if item.get("hard_negative"):
                split_counts[split]["hard_negative_tiles"] += 1
                source_counts[(split, dataset)]["hard_negative_tiles"] += 1

    if expected_tiles is not None and rows_seen != expected_tiles:
        if isinstance(summary, dict) and isinstance(summary.get("datasets"), list):
            source_rows = [
                {
                    "split": "all",
                    "dataset": item["dataset"],
                    "tiles": int(item.get("tiles_written") or 0),
                    "labels": int(item.get("labels_written") or 0),
                    "hard_negative_tiles": "",
                }
                for item in summary["datasets"]
            ]
            write_csv(
                yolo_root / "source_distribution.csv",
                source_rows,
                ["split", "dataset", "tiles", "labels", "hard_negative_tiles"],
            )
        return {
            "manifest_found": True,
            "manifest_rows": rows_seen,
            "manifest_expected_tiles": expected_tiles,
            "manifest_matches_summary": False,
            "manifest_reports_written": False,
        }

    split_rows = [
        {
            "split": split,
            "tiles": int(counts["tiles"]),
            "labels": int(counts["labels"]),
            "hard_negative_tiles": int(counts["hard_negative_tiles"]),
        }
        for split, counts in sorted(split_counts.items())
    ]
    source_rows = [
        {
            "split": split,
            "dataset": dataset,
            "tiles": int(counts["tiles"]),
            "labels": int(counts["labels"]),
            "hard_negative_tiles": int(counts["hard_negative_tiles"]),
        }
        for (split, dataset), counts in sorted(source_counts.items())
    ]

    write_csv(
        yolo_root / "source_distribution.csv",
        source_rows,
        ["split", "dataset", "tiles", "labels", "hard_negative_tiles"],
    )
    (yolo_root / "split_summary.json").write_text(
        json.dumps({"splits": split_rows, "manifest_rows": rows_seen}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "manifest_found": True,
        "manifest_rows": rows_seen,
        "manifest_matches_summary": True,
        "manifest_reports_written": True,
        "split_rows": len(split_rows),
        "source_rows": len(source_rows),
    }


def label_file_reports(yolo_root: Path, names: list[str]) -> dict[str, Any]:
    class_counts: dict[tuple[str, str], int] = defaultdict(int)
    label_files = 0
    for split in ("train", "val", "test"):
        label_dir = yolo_root / split / "labels"
        if not label_dir.exists():
            continue
        for label_path in label_dir.glob("*.txt"):
            label_files += 1
            for line in label_path.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if not parts:
                    continue
                try:
                    class_id = int(parts[0])
                except ValueError:
                    continue
                class_name = names[class_id] if 0 <= class_id < len(names) else f"class_{class_id}"
                class_counts[(split, class_name)] += 1

    if not class_counts:
        return {"label_files_found": label_files, "class_distribution_written": False}

    rows = [
        {"split": split, "class": class_name, "labels": count}
        for (split, class_name), count in sorted(class_counts.items())
    ]
    write_csv(yolo_root / "class_distribution.csv", rows, ["split", "class", "labels"])
    return {"label_files_found": label_files, "class_distribution_written": True, "class_rows": len(rows)}


def write_taxonomy(yolo_root: Path, names: list[str], report: dict[str, Any]) -> None:
    policy = active_detection_policy()
    parent_dataset = set(names) == set(DEFENSE_PARENT_CLASSES)
    class_map = []
    parent_counts = Counter()
    disabled = set(policy["disabled_parent_classes"])
    for index, name in enumerate(names):
        parent = parent_class_for_label(name)
        parent_counts[parent] += 1
        class_map.append({
            "id": index,
            "original_class": name,
            "parent_class": parent,
            "enabled": parent not in disabled and parent in set(policy["enabled_parent_classes"]),
        })

    taxonomy = {
        "taxonomy": "optical-defense" if parent_dataset else "source-with-optical-defense-mapping",
        "taxonomy_version": TAXONOMY_VERSION,
        "dataset_label_space": "parent" if parent_dataset else "source",
        "note": (
            "This taxonomy.json was repaired from copied metadata for a collapsed optical-defense dataset."
            if parent_dataset
            else (
                "This taxonomy.json was repaired from an old copied dataset. The label files still use "
                "source-specific classes, not the collapsed optical-defense training classes. Use it for "
                "audit/inference mapping, but regenerate the dataset for robust retraining."
            )
        ),
        "defense_parent_classes": list(DEFENSE_PARENT_CLASSES),
        "distractor_parent_classes": list(DISTRACTOR_PARENT_CLASSES),
        "disabled_parent_classes": policy["disabled_parent_classes"],
        "threshold_profile": policy["threshold_profile"],
        "class_count": len(names),
        "parent_class_counts": dict(sorted(parent_counts.items())),
        "classes": class_map,
        "repair_report": report,
        "recommended_rebuild_command": (
            "python inference/prepare_datasets.py --datasets xview dota fair1m dior sodaa hrsc2016 "
            "--tile-size 1024 --overlap 0.2 --include-empty-ratio 0.05 --hard-negative-ratio 0.5 "
            "--max-instances-per-class 50000 --clean"
        ),
    }
    (yolo_root / "taxonomy.json").write_text(json.dumps(taxonomy, indent=2) + "\n", encoding="utf-8")

    mapping_rows = [
        {
            "id": item["id"],
            "original_class": item["original_class"],
            "parent_class": item["parent_class"],
            "enabled": item["enabled"],
        }
        for item in class_map
    ]
    write_csv(yolo_root / "class_mapping.csv", mapping_rows, ["id", "original_class", "parent_class", "enabled"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair copied YOLO metadata artifacts.")
    parser.add_argument("--yolo-root", type=Path, default=DEFAULT_YOLO_ROOT)
    parser.add_argument("--no-fix-path", action="store_true", help="Do not rewrite data.yaml path to the local machine.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    yolo_root = args.yolo_root.resolve()
    names = read_classes(yolo_root)
    path_changed = False if args.no_fix_path else fix_data_yaml_path(yolo_root)
    report = {
        "data_yaml_path_rewritten": path_changed,
        **manifest_reports(yolo_root),
        **label_file_reports(yolo_root, names),
    }
    write_taxonomy(yolo_root, names, report)
    print(json.dumps({"yolo_root": str(yolo_root), "classes": len(names), **report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
