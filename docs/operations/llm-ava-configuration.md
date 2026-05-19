# Operations — LLM (Ava) Configuration

## Ava is optional

Sentinel runs fully without an LLM. When unset, AI-backed endpoints return a stable 503 and the frontend hides Ava features.

## Local vLLM / Ollama

`.env`:

```
OPENAI_API_BASE=http://host.docker.internal:8000/v1   # or use llm-local-proxy below
OPENAI_API_KEY=dummy   # most local runtimes ignore
OPENAI_MODEL=google/gemma-4-31b-it
```

## Reaching a host-side runtime

If your runtime binds to `127.0.0.1` on the host, containers can't reach it. Enable the optional `llm-local-proxy` compose profile:

```bash
docker compose --profile llm-proxy up -d
```

This starts an `alpine/socat:1.8.0.3` forwarder on host port 18001 that proxies into the container network.

`.env`:

```
OPENAI_API_BASE=http://llm-local-proxy:8000/v1
```

## Behavior when offline

- `GET /api/health` reports `llm: false`.
- `POST /api/ai/*` returns 503.
- Frontend gates Ava UI features.
- Everything else (ingest, inference, ontology, FMV, analytics) is unaffected.

## Toggle LLM post-classification

```
ENABLE_LLM_DETECTION_CLASSIFICATION=true
```

Setting to `false` disables LLM-driven post-classification of detections (useful for benchmarks where determinism matters).

## Cross-references

- [backend/ai-llm-integration.md](../backend/ai-llm-integration.md)
- [backend-routers/ai-router.md](../backend-routers/ai-router.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
