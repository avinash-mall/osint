import os
from dotenv import load_dotenv
from database import db

load_dotenv()

OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "google/gemma-4-31B-it")


class AIUnavailable(RuntimeError):
    pass


def ai_status() -> dict:
    return {
        "configured": bool(OPENAI_API_BASE),
        "model": OPENAI_MODEL,
        "mode": "read_only_graph_summary",
    }


def get_ai_response(question: str) -> str:
    """
    Return a read-only graph summary for the analyst query.

    The previous GraphCypherQAChain path allowed arbitrary generated Cypher. This
    keeps the chat useful while limiting database access to fixed read queries.
    """
    text = question.lower()
    if "target" in text:
        query = """
            MATCH (t:Target)
            RETURN t.name AS name, t.priority AS priority, t.status AS status,
                   t.latitude AS latitude, t.longitude AS longitude
            ORDER BY CASE t.priority WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 1 ELSE 0 END DESC,
                     t.name ASC
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
        raise AIUnavailable("SentinelOS AI could not read the ontology right now.") from e

    if not rows:
        return f"{heading}: no matching records found."

    lines = [heading + ":"]
    for row in rows[:10]:
        compact = ", ".join(f"{key}={value}" for key, value in row.items() if value is not None)
        lines.append(f"- {compact}")
    return "\n".join(lines)
