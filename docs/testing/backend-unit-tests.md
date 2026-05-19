# Backend Unit Tests

**Path:** [backend/tests/](../../backend/tests/)
**Runner:** `cd backend && python -m pytest tests/ -q`
**Config:** [pytest.ini](../../pytest.ini)

## Tests

| File | Covers |
|---|---|
| [test_ai_json_parsing.py](../../backend/tests/test_ai_json_parsing.py) | `get_llm_json` / `extract_json_object` across fenced / strict / prose-wrapped responses |
| [test_auth.py](../../backend/tests/test_auth.py) | Session signing, LDAP binds, admin checks |
| [test_chip_emitter.py](../../backend/tests/test_chip_emitter.py) | Imagery chip planning, window generation |
| [test_debias_units.py](../../backend/tests/test_debias_units.py) | Candidate-link scoring, multi-pass ranking |
| [test_object_details.py](../../backend/tests/test_object_details.py) | Threat/affiliation validation, `object_details` upsert |
| [test_ontology.py](../../backend/tests/test_ontology.py) | Label normalization, cache invalidation, unknown-label logging |
| [test_ontology_api.py](../../backend/tests/test_ontology_api.py) | Branch/object/prompt API endpoints |
| [test_precision_inference_policy.py](../../backend/tests/test_precision_inference_policy.py) | Precision policy defaults and source-layer calibration tag selection |
| [test_size_estimation.py](../../backend/tests/test_size_estimation.py) | OBB → length/width/area/bearing in UTM-local |

## conftest

[conftest.py](../../backend/tests/conftest.py) sets test-mode env defaults and provides a Postgres fixture for the API tests.

## What's NOT covered here

Worker pipeline integration is covered by end-to-end runs and the benchmark harness — see [benchmark-harness.md](benchmark-harness.md). Splitting `worker_legacy.py` for unit testing is gated on the refactor described in [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md).

## Cross-references

- [conventions/error-handling.md](../conventions/error-handling.md)
- [fixtures-and-test-data.md](fixtures-and-test-data.md)
