from __future__ import annotations

import ai


def load_ai_json_helpers():
    """Expose the JSON helpers under the dict shape the tests already use.

    Previously this exec'd a *line-sliced* fragment of ai.py in a bare namespace,
    which broke whenever the slice referenced a module-level constant defined
    outside it (e.g. ``LLM_JSON_TIMEOUT_SECONDS`` used as a default arg). Importing
    the module is offline-safe (no network/DB at import) and not brittle.
    """
    return {"extract_json_object": ai.extract_json_object, "AIUnavailable": ai.AIUnavailable}


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
