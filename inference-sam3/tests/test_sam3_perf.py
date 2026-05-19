"""Verify the chunked-batched path is skipped for many-prompt cases."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

# Ensure inference-sam3 root is importable regardless of cwd.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_bundle():
    """Construct a bundle whose lock / processor / model behave well enough
    for the dispatch path under test without needing a real GPU.
    """
    fake_processor = MagicMock()
    fake_processor.set_image.return_value = {"backbone_out": "cached"}
    fake_processor.set_text_prompt.return_value = {"scores": [], "boxes": [], "masks": []}

    class _NullLock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return {
        "sam3_image": {"processor": fake_processor, "model": MagicMock()},
        "device": "cpu",
        "lock": _NullLock(),
    }, fake_processor


from contextlib import nullcontext


def _patch_torch_only_helpers(monkeypatch, sam3_runner):
    """Replace torch-using context helpers with nullcontext so tests can run
    on a venv without torch."""
    monkeypatch.setattr(sam3_runner, "_inference_mode", lambda: nullcontext())
    monkeypatch.setattr(sam3_runner, "_autocast_ctx", lambda _d: nullcontext())


def test_many_prompts_skip_batched_path(monkeypatch):
    """When _cached_batched_supported is False (dev / unpatched env) AND
    prompts > CHUNK_SIZE, _run_text_prompts_batched runs once per chunk —
    the historical chunked-batched fallback. (When the upstream cached
    patch is installed inside the inference container, the cached batched
    path supersedes this; that path is exercised live by the benchmark.)
    """
    import sam3_runner

    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT", True)
    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT_CHUNK_SIZE", 4)
    monkeypatch.setattr(sam3_runner, "_cached_batched_supported", lambda _b: False)

    bundle, processor = _make_bundle()

    with patch.object(sam3_runner, "_run_text_prompts_batched", return_value=[]) as batched_spy:
        sam3_runner.run_text_prompts(
            bundle, np.zeros((64, 64, 3), dtype=np.uint8),
            [f"obj_{i}" for i in range(10)], score_threshold=0.5,
        )

    # 10 prompts, chunk=4 → 3 chunks: [0:4], [4:8], [8:10].
    assert batched_spy.call_count == 3, (
        f"expected 3 chunked calls, got {batched_spy.call_count}"
    )


def test_many_prompts_cached_batched_path(monkeypatch):
    """When the cached-encoder patch IS installed, the dispatch routes to
    _run_text_prompts_cached_batched and skips the chunked path entirely.
    """
    import sam3_runner

    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT", True)
    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT_CHUNK_SIZE", 4)
    monkeypatch.setattr(sam3_runner, "_cached_batched_supported", lambda _b: True)

    bundle, _ = _make_bundle()

    with patch.object(sam3_runner, "_run_text_prompts_batched") as batched_spy, \
         patch.object(sam3_runner, "_run_text_prompts_cached_batched", return_value=[]) as cached_spy:
        sam3_runner.run_text_prompts(
            bundle, np.zeros((64, 64, 3), dtype=np.uint8),
            [f"obj_{i}" for i in range(10)], score_threshold=0.5,
        )

    assert batched_spy.call_count == 0, "chunked-batched fallback must NOT run"
    assert cached_spy.call_count == 1, "cached-batched path must run exactly once"


def test_few_prompts_keep_batched_path(monkeypatch):
    """When prompts <= CHUNK_SIZE, the batched path is still used."""
    import sam3_runner

    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT", True)
    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT_CHUNK_SIZE", 8)

    bundle, _ = _make_bundle()

    with patch.object(sam3_runner, "_run_text_prompts_batched", return_value=[]) as batched_spy:
        sam3_runner.run_text_prompts(
            bundle, np.zeros((64, 64, 3), dtype=np.uint8),
            ["a", "b", "c", "d"], score_threshold=0.5,
        )

    assert batched_spy.call_count == 1, "few-prompt path must use batched dispatch once"


def test_single_prompt_uses_loop_not_batched(monkeypatch):
    """One prompt → loop path (no point in batching a single query)."""
    import sam3_runner

    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT", True)
    monkeypatch.setattr(sam3_runner, "SAM3_BATCHED_TEXT_CHUNK_SIZE", 8)
    monkeypatch.setattr(sam3_runner, "_prompt_passes_category_gate", lambda *_a, **_k: True)
    _patch_torch_only_helpers(monkeypatch, sam3_runner)

    bundle, processor = _make_bundle()

    with patch.object(sam3_runner, "_run_text_prompts_batched") as batched_spy:
        sam3_runner.run_text_prompts(
            bundle, np.zeros((64, 64, 3), dtype=np.uint8),
            ["solo"], score_threshold=0.5,
        )

    assert batched_spy.call_count == 0
    assert processor.set_image.call_count == 1
    assert processor.set_text_prompt.call_count == 1


def test_stage_timer_accumulates():
    """stage_timer must add to existing entries so multi-chunk loops report
    cumulative ms, not just the last iteration."""
    from sam3_perf import stage_timer

    timings: dict[str, float] = {}
    with stage_timer(timings, "x"):
        pass
    first = timings["x"]
    with stage_timer(timings, "x"):
        pass
    assert timings["x"] >= first, "accumulation lost"
