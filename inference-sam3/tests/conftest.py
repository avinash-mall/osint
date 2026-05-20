"""Shared test bootstrap for the inference-sam3 unit suite.

Two responsibilities:

1. Put ``inference-sam3/`` on ``sys.path`` so test files can ``import main``,
   ``import sam3_runner`` etc. without each having to repeat the boilerplate.
   This also makes the suite collectable when pytest is invoked from the
   repository root, not only from ``cd inference-sam3``.

2. Pre-stub ``psutil`` and ``torch`` in ``sys.modules`` when not already
   installed. ``main.py`` imports both at module scope; the full image carries
   them, but lightweight CPU-only test environments do not. Stubbing them
   here (instead of inside individual test files) removes the implicit
   ordering dependency where one test must run before another to seed
   ``sys.modules`` for it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "psutil" not in sys.modules:
    sys.modules["psutil"] = types.SimpleNamespace(
        cpu_percent=lambda interval=None: 0.0,
        virtual_memory=lambda: types.SimpleNamespace(percent=0.0, available=0, total=0),
        disk_usage=lambda path: types.SimpleNamespace(percent=0.0, free=0, total=0),
    )

if "torch" not in sys.modules:
    _cuda = types.SimpleNamespace(
        is_available=lambda: False,
        current_device=lambda: 0,
        reset_peak_memory_stats=lambda *_a, **_k: None,
        max_memory_allocated=lambda *_a, **_k: 0,
    )
    sys.modules["torch"] = types.SimpleNamespace(
        cuda=_cuda,
        backends=types.SimpleNamespace(
            cuda=types.SimpleNamespace(matmul=types.SimpleNamespace(allow_tf32=False)),
            cudnn=types.SimpleNamespace(allow_tf32=False, benchmark=False),
        ),
        set_float32_matmul_precision=lambda *_a, **_k: None,
        get_float32_matmul_precision=lambda: "highest",
    )
