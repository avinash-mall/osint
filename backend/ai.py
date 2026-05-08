import os
import json
import re
import requests
from urllib.parse import urlsplit, urlunsplit
from dotenv import load_dotenv
from database import db

load_dotenv()

OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "google/gemma-4-31B-it")


class AIUnavailable(RuntimeError):
    pass


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_JSON_START_RE = re.compile(r"[\[{]")


def _chat_completion_urls(api_base: str) -> list[str]:
    base = api_base.rstrip("/")
    primary = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    urls = [primary]

    parsed = urlsplit(primary)
    if parsed.hostname in {"host.docker.internal", "localhost", "127.0.0.1"}:
        fallback_hosts = {
            "host.docker.internal": ["localhost", "127.0.0.1"],
            "localhost": ["host.docker.internal", "127.0.0.1"],
            "127.0.0.1": ["localhost", "host.docker.internal"],
        }[parsed.hostname]
        for host in fallback_hosts:
            netloc = host
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            fallback = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
            if fallback not in urls:
                urls.append(fallback)

    return urls


def ai_status() -> dict:
    return {
        "configured": bool(OPENAI_API_BASE),
        "model": OPENAI_MODEL,
        "mode": "read_only_graph_summary",
    }


def get_llm_text(
    prompt: str,
    system: str = "",
    max_tokens: int = 400,
    temperature: float = 0.1,
    timeout_seconds: float = 8,
) -> str:
    if not OPENAI_API_BASE:
        raise AIUnavailable("LLM classification is unavailable because OPENAI_API_BASE is not configured.")

    headers = {"Content-Type": "application/json"}
    if OPENAI_API_KEY:
        headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system or "You are a concise intelligence UI classification assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    last_error = None
    for url in _chat_completion_urls(OPENAI_API_BASE):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            body = response.json()
            content = body.get("choices", [{}])[0].get("message", {}).get("content")
            if not content:
                content = body.get("choices", [{}])[0].get("text")
            if not content:
                raise AIUnavailable("LLM returned an empty classification response.")
            return str(content).strip()
        except requests.RequestException as exc:
            last_error = exc
            continue
    raise AIUnavailable("LLM classification request failed.") from last_error


def get_llm_json(prompt: str, system: str = "", max_tokens: int = 500, timeout_seconds: float = 8) -> dict:
    content = get_llm_text(prompt, system=system, max_tokens=max_tokens, temperature=0, timeout_seconds=timeout_seconds)
    parsed = extract_json_object(content)
    if not isinstance(parsed, dict):
        raise AIUnavailable("LLM classification response JSON was not an object.")
    return parsed


def extract_json_object(content: str) -> dict:
    """Extract the first JSON object from an LLM response.

    Handles strict JSON, fenced JSON blocks, and prose-wrapped responses without
    relying on a first-brace/last-brace slice that can span unrelated blocks.
    """
    candidates = [str(content or "").strip()]
    candidates.extend(match.group(1).strip() for match in _FENCED_JSON_RE.finditer(str(content or "")))
    decoder = json.JSONDecoder()

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                return parsed

    for candidate in candidates:
        for match in _JSON_START_RE.finditer(candidate):
            try:
                parsed, _end = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

    raise AIUnavailable("LLM classification response was not valid JSON.")


def get_ai_response(question: str) -> str:
    """
    Return a read-only graph summary for the analyst query.

    The previous GraphCypherQAChain path allowed arbitrary generated Cypher. This
    keeps the chat useful while limiting database access to fixed read queries.
    """
    text = question.lower()
    if "target" in text:
        query = """
            MATCH (t)
            WHERE 'Target' IN labels(t)
            WITH properties(t) AS props
            RETURN props.name AS name, props.priority AS priority, props.status AS status,
                   props.latitude AS latitude, props.longitude AS longitude
            ORDER BY CASE coalesce(props.priority, '') WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 1 ELSE 0 END DESC,
                     coalesce(props.name, '') ASC
            LIMIT 10
        """
        heading = "Top targets"
    elif "satellite" in text or "constellation" in text or "space" in text:
        query = """
            MATCH (s:Satellite)
            RETURN s.name AS name, s.type AS type, s.status AS status, s.orbit_alt AS orbit_alt
            ORDER BY s.name ASC
            LIMIT 10
        """
        heading = "Constellation assets"
    elif "detection" in text:
        query = """
            MATCH (d:Detection)
            RETURN d.class AS class, d.confidence AS confidence, d.latitude AS latitude,
                   d.longitude AS longitude, d.postgis_id AS id
            ORDER BY d.confidence DESC
            LIMIT 10
        """
        heading = "Recent detections"
    elif "asset" in text or "vessel" in text or "aircraft" in text:
        query = """
            MATCH (a:Asset)
            OPTIONAL MATCH (a)-[:OBSERVED_AT]->(o:Observation)
            WITH a, o ORDER BY o.timestamp DESC
            RETURN a.id AS id, a.callsign AS callsign, labels(a) AS labels,
                   collect(o)[0].latitude AS latitude, collect(o)[0].longitude AS longitude
            LIMIT 10
        """
        heading = "Tracked assets"
    else:
        query = """
            MATCH (n)
            RETURN labels(n)[0] AS label, count(*) AS count
            ORDER BY count DESC
        """
        heading = "Ontology summary"

    try:
        with db.get_session() as session:
            rows = [dict(record) for record in session.run(query)]
    except Exception as e:
        raise AIUnavailable("Sentinel AI could not read the ontology right now.") from e

    if not rows:
        return f"{heading}: no matching records found."

    lines = [heading + ":"]
    for row in rows[:10]:
        compact = ", ".join(f"{key}={value}" for key, value in row.items() if value is not None)
        lines.append(f"- {compact}")
    return "\n".join(lines)
