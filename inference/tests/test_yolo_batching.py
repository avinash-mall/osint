from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


class FakeTensor:
    def __init__(self, values):
        self.values = np.array(values)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class FakeObb:
    xyxyxyxy = FakeTensor([[[10, 20], [50, 20], [50, 60], [10, 60]]])
    cls = FakeTensor([0])
    conf = FakeTensor([0.8])


class FakeObbResult:
    obb = FakeObb()
    boxes = None


class FakeModelWithStringNames:
    names = {"0": "airplane"}


class FakeBox:
    def __init__(self):
        self.xyxy = np.array([[20, 30, 80, 90]], dtype=float)
        self.cls = np.array([0])
        self.conf = np.array([0.82])


class FakeBoxResult:
    obb = None
    boxes = [FakeBox()]


def test_detections_from_yolo_result_parses_obb():
    detections = main.detections_from_yolo_result(
        FakeObbResult(),
        main._model_names(FakeModelWithStringNames()),
        (100, 100),
    )

    assert len(detections) == 1
    assert detections[0]["parent_class"] == "aircraft"
    assert detections[0]["bbox"] == [0.3, 0.4, 0.4, 0.4]
    assert detections[0]["obb"] == [0.1, 0.2, 0.5, 0.2, 0.5, 0.6, 0.1, 0.6]


def test_detections_from_yolo_result_parses_boxes():
    detections = main.detections_from_yolo_result(
        FakeBoxResult(),
        {0: "cargo_truck"},
        (100, 100),
    )

    assert len(detections) == 1
    assert detections[0]["parent_class"] == "vehicle"
    assert detections[0]["bbox"] == [0.5, 0.6, 0.6, 0.6]


def test_yolo_batcher_full_batch(monkeypatch):
    async def scenario():
        batcher = main.YoloInferenceBatcher(max_size=2, timeout_ms=100)

        def fake_run_yolo_batch(items):
            return [
                {"status": "success", "detections": [], "batch_size": len(items)}
                for _ in items
            ]

        monkeypatch.setattr(main, "run_yolo_batch", fake_run_yolo_batch)
        image = main.Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8), mode="RGB")
        left, right = await asyncio.gather(
            batcher.submit(image, {}),
            batcher.submit(image, {}),
        )

        assert left["batch_size"] == 2
        assert right["batch_size"] == 2
        assert batcher.stats()["total_batches"] == 1
        assert batcher.stats()["avg_batch_size"] == 2
        batcher._worker_task.cancel()

    asyncio.run(scenario())


def test_yolo_batcher_timeout_flush(monkeypatch):
    async def scenario():
        batcher = main.YoloInferenceBatcher(max_size=4, timeout_ms=1)

        def fake_run_yolo_batch(items):
            return [
                {"status": "success", "detections": [], "batch_size": len(items)}
                for _ in items
            ]

        monkeypatch.setattr(main, "run_yolo_batch", fake_run_yolo_batch)
        image = main.Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8), mode="RGB")
        result = await batcher.submit(image, {})

        assert result["batch_size"] == 1
        assert batcher.stats()["total_batches"] == 1
        batcher._worker_task.cancel()

    asyncio.run(scenario())


def test_yolo_batcher_exception_propagates(monkeypatch):
    async def scenario():
        batcher = main.YoloInferenceBatcher(max_size=1, timeout_ms=0)

        def fake_run_yolo_batch(items):
            raise RuntimeError("boom")

        monkeypatch.setattr(main, "run_yolo_batch", fake_run_yolo_batch)
        image = main.Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8), mode="RGB")
        try:
            await batcher.submit(image, {})
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected RuntimeError")
        batcher._worker_task.cancel()

    asyncio.run(scenario())
