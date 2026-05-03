from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


def test_mmrotate_parser_emits_dota_rotated_box():
    result = [
        np.array([[50, 60, 40, 20, 0.0, 0.8]], dtype=float),
        *[np.empty((0, 6), dtype=float) for _ in range(len(main.DOTA_CLASSES) - 1)],
    ]

    detections = main.detections_from_mmrotate_result(result, (100, 100))

    assert len(detections) == 1
    assert detections[0]["parent_class"] == "aircraft"
    assert detections[0]["original_class"] == "plane"
    assert detections[0]["bbox"] == [0.5, 0.6, 0.4, 0.2]
    assert detections[0]["obb"] == [0.3, 0.5, 0.7, 0.5, 0.7, 0.7, 0.3, 0.7]


def test_mmrotate_parser_filters_low_confidence():
    result = [
        np.array([[50, 60, 40, 20, 0.0, 0.01]], dtype=float),
        *[np.empty((0, 6), dtype=float) for _ in range(len(main.DOTA_CLASSES) - 1)],
    ]

    assert main.detections_from_mmrotate_result(result, (100, 100)) == []


def test_mmrotate_parser_suppresses_distractors():
    tennis_index = main.DOTA_CLASSES.index("tennis-court")
    result = [np.empty((0, 6), dtype=float) for _ in range(len(main.DOTA_CLASSES))]
    result[tennis_index] = np.array([[50, 60, 40, 20, 0.0, 0.95]], dtype=float)

    assert main.detections_from_mmrotate_result(result, (100, 100)) == []


def test_mmrotate_parser_handles_datasample_pred_instances():
    class PredInstances:
        bboxes = np.array([[50, 60, 40, 20, 0.0]], dtype=float)
        scores = np.array([0.8], dtype=float)
        labels = np.array([main.DOTA_CLASSES.index("ship")], dtype=int)

    class DataSample:
        pred_instances = PredInstances()

    detections = main.detections_from_mmrotate_result(DataSample(), (100, 100))

    assert len(detections) == 1
    assert detections[0]["parent_class"] == "ship"
