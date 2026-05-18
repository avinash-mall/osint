"""Internal eval runner — filters LAYER_CONFIGS to skip a list of configs.

Workaround for the running container's GroundingDINO instability that takes
the inference service down mid-eval ("Issue found, reverting to CPU mode!"
in the inference-sam3 logs). Phase 8.38 default-disabled GD precisely
because of this kind of failure mode.

Usage: identical to compare_inference_layers.py plus ``--skip-config``
(can be passed multiple times). The flag is stripped before delegating.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Strip our extension before the underlying argparse sees argv.
argv = sys.argv[1:]
skipped: list[str] = []
filtered: list[str] = []
i = 0
while i < len(argv):
    if argv[i] == "--skip-config" and i + 1 < len(argv):
        skipped.append(argv[i + 1])
        i += 2
    else:
        filtered.append(argv[i])
        i += 1
sys.argv = [sys.argv[0]] + filtered

import scripts.compare_inference_layers as cil  # noqa: E402

if skipped:
    cil.LAYER_CONFIGS = [c for c in cil.LAYER_CONFIGS if c["config_name"] not in skipped]
    cil.SEGMENTER_CONFIGS = [c for c in cil.SEGMENTER_CONFIGS if c["config_name"] not in skipped]
    print(f"[_eval_runner] skipped configs: {skipped}")
    print(f"[_eval_runner] remaining LAYER_CONFIGS: {[c['config_name'] for c in cil.LAYER_CONFIGS]}")

raise SystemExit(cil.main())
