from __future__ import annotations

import json
import re
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


def load_ai_json_helpers():
    path = BACKEND_DIR / "ai.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("class AIUnavailable")
    end = source.index("def get_ai_response", start)
    namespace = {"json": json, "re": re}
    exec(source[start:end], namespace)
    return namespace


def test_extract_json_object_accepts_strict_json():
    helpers = load_ai_json_helpers()

    assert helpers["extract_json_object"]('{"label": "Airfield"}') == {"label": "Airfield"}


def test_extract_json_object_reads_fenced_json_without_trailing_prose():
    helpers = load_ai_json_helpers()
    content = """Here is the result:

```json
{"label": "Radar", "confidence": 0.8}
```

Review complete.
"""

    assert helpers["extract_json_object"](content) == {"label": "Radar", "confidence": 0.8}


def test_extract_json_object_does_not_span_multiple_json_blocks():
    helpers = load_ai_json_helpers()
    content = 'First: {"label": "Ship"}\nSecond: {"label": "Aircraft"}'

    assert helpers["extract_json_object"](content) == {"label": "Ship"}


def test_extract_json_object_rejects_responses_without_json_object():
    helpers = load_ai_json_helpers()

    try:
        helpers["extract_json_object"]("no JSON here")
    except helpers["AIUnavailable"]:
        return

    raise AssertionError("Expected AIUnavailable for non-JSON response")
