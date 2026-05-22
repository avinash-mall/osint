# `backend/ai.py` — LLM Client (Ava)

**Path:** [backend/ai.py](../../backend/ai.py)
**Lines:** ~204
**Depends on:** Env `OPENAI_API_BASE`, `OPENAI_API_KEY` (default `dummy`), `OPENAI_MODEL` (default `google/gemma-4-31b-it`)

## Purpose

OpenAI-compatible chat-completions client for: analyst Q&A, structured extraction from free text, candidate-link ranking suggestions, action proposals, ontology bulk-edit proposals. Targets a **local** vLLM/Ollama endpoint, not public OpenAI.

## Why this design

- **Graceful degradation** — every entry raises `AIUnavailable` when endpoint unset/unreachable. Routes catch → 503 stable shape; rest of app unaffected.
- **Read-only DB intent** — [`get_ai_response`](../../backend/ai.py#L136) builds context by reading DB but never lets the LLM generate/execute Cypher/SQL. Arbitrary LLM-driven queries = critical injection vector.
- **JSON-mode fallback ladder** — `get_llm_json` → `extract_json_object`: native JSON mode → markdown code-fenced JSON → strict prose-wrapped JSON. Tests: [backend/tests/test_ai_json_parsing.py](../../backend/tests/test_ai_json_parsing.py).
- **Multiple chat URLs tried** — `_chat_completion_urls` covers `/v1/chat/completions`, `/chat/completions`, bare `/completions` → same client works against different self-hosted runtimes without config.

## Key symbols

- [`AIUnavailable`](../../backend/ai.py#L16) — RuntimeError subclass.
- [`ai_status`](../../backend/ai.py#L47) — used by [`/api/health`](../backend-routers/health-router.md).
- [`get_llm_text`](../../backend/ai.py#L55), [`get_llm_json`](../../backend/ai.py#L95), [`extract_json_object`](../../backend/ai.py#L103).
- [`get_ai_response`](../../backend/ai.py#L136) — high-level Q&A.

## Cross-references

- [backend-routers/ai-router.md](../backend-routers/ai-router.md)
- [operations/llm-ava-configuration.md](../operations/llm-ava-configuration.md)
- Tests: [backend/tests/test_ai_json_parsing.py](../../backend/tests/test_ai_json_parsing.py)
