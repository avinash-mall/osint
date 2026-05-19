# `backend/ai.py` — LLM Client (Ava)

**Path:** [backend/ai.py](../../backend/ai.py)
**Lines:** ~204
**Depends on:** Env `OPENAI_API_BASE`, `OPENAI_API_KEY` (default `dummy`), `OPENAI_MODEL` (default `google/gemma-4-31b-it`)

## Purpose

OpenAI-compatible chat-completions client used for: analyst-question answering, structured extraction from free text, candidate-link ranking suggestions, action proposals, and ontology bulk-edit proposals. Designed to work against a **local** vLLM/Ollama endpoint, not the public OpenAI API.

## Why this design

- **Graceful degradation.** Every entry raises `AIUnavailable` when the endpoint is unset or unreachable. Routes catch it and return 503 with a stable shape; the rest of the app is unaffected.
- **Read-only DB intent.** [`get_ai_response`](../../backend/ai.py#L136) builds context by reading from the DB but never lets the LLM generate or execute Cypher/SQL. Arbitrary query execution from LLM output would be a critical injection vector.
- **JSON-mode fallback ladder.** `get_llm_json` calls `extract_json_object` which handles: native JSON mode → markdown code-fenced JSON → strict prose-wrapped JSON. Tests in [backend/tests/test_ai_json_parsing.py](../../backend/tests/test_ai_json_parsing.py).
- **Multiple chat URLs tried.** `_chat_completion_urls` covers `/v1/chat/completions`, `/chat/completions`, and bare `/completions` so the same client works against different self-hosted runtimes without configuration.

## Key symbols

- [`AIUnavailable`](../../backend/ai.py#L16) — RuntimeError subclass.
- [`ai_status`](../../backend/ai.py#L47) — used by [`/api/health`](../backend-routers/health-router.md).
- [`get_llm_text`](../../backend/ai.py#L55), [`get_llm_json`](../../backend/ai.py#L95), [`extract_json_object`](../../backend/ai.py#L103).
- [`get_ai_response`](../../backend/ai.py#L136) — high-level Q&A.

## Cross-references

- [backend-routers/ai-router.md](../backend-routers/ai-router.md)
- [operations/llm-ava-configuration.md](../operations/llm-ava-configuration.md)
- Tests: [backend/tests/test_ai_json_parsing.py](../../backend/tests/test_ai_json_parsing.py)
