"""AI assist + action-proposal lifecycle routes.

The execute path can reach pre-existing implementations on the FastAPI app
via `create_target_package` / `create_collection_requirement` / `run_viewshed`
when those exist. They're called lazily so the import surface stays small.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ai import AIUnavailable, get_ai_response, get_llm_json
from database import db, postgis_db
from events import normalize_domain, publish_event, record_timeline_event
from platform_schema import ensure_platform_tables
from schemas import AIActionProposalRequest, AIAnalysisRequest, AnalyticsRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/ai/analyze")
def ai_analyze(req: AIAnalysisRequest):
    ensure_platform_tables()
    try:
        reply = get_ai_response(req.prompt)
    except AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    domain = normalize_domain(req.domain, "WORKFLOW")
    analysis = {
        "summary": reply,
        "citations": [
            {"type": "ontology", "label": "Neo4j read-only summary"},
            {"type": "context", "label": req.entity_id or domain},
        ],
        "next_actions": [
            {"action_type": "generate_report", "label": "Draft intelligence summary", "requires_approval": True},
            {"action_type": "create_requirement", "label": "Create collection requirement", "requires_approval": True},
        ],
        "policy": "human_approval_required",
    }
    record_timeline_event(domain, "ai_analysis", "AI analysis generated", {"prompt": req.prompt, "entity_id": req.entity_id})
    return {"analysis": analysis, "status": "ok"}


_EXTRACT_TYPES = ("Person", "Place", "Asset", "Org", "Event", "Vessel", "Aircraft", "Other")


@router.post("/api/ai/extract")
def ai_extract(req: AIAnalysisRequest):
    ensure_platform_tables()
    text = (req.prompt or "").strip()
    if not text and req.context:
        text = json.dumps(req.context, default=str)
    if not text:
        return {"entities": [], "citations": [], "status": "empty_input"}

    system = (
        "You are an OSINT entity-extraction assistant. Return strict JSON: "
        '{"entities": [{"label": <surface form>, "type": <one of '
        + ", ".join(_EXTRACT_TYPES)
        + '>, "confidence": <0..1>}]}. Drop generic stopwords. Cap at 20 entities.'
    )
    try:
        data = get_llm_json(prompt=f"Extract entities from:\n{text}", system=system, max_tokens=600)
    except AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    raw = data.get("entities") or []
    entities = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        type_ = str(item.get("type") or "Other")
        if type_ not in _EXTRACT_TYPES:
            type_ = "Other"
        try:
            conf = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        entities.append({"label": label[:200], "type": type_, "confidence": max(0.0, min(1.0, conf))})
    entities = entities[:20]
    record_timeline_event(
        normalize_domain(req.domain, "WORKFLOW"),
        "ai_extract",
        f"Extracted {len(entities)} entities",
        {"input_chars": len(text)},
        entity_id=req.entity_id,
    )
    return {
        "entities": entities,
        "citations": [{"type": "input", "label": "submitted text/context"}],
        "status": "ok",
    }


def _fetch_target_summaries(limit: int = 25) -> list[dict]:
    try:
        with db.get_session() as session:
            rows = session.run(
                """
                MATCH (t:Target)
                WHERE t.name IS NOT NULL
                RETURN coalesce(t.id, elementId(t)) AS id,
                       t.name AS name,
                       t.type AS type,
                       t.category AS category,
                       t.priority AS priority
                ORDER BY coalesce(t.priority, '') DESC, t.name ASC
                LIMIT $limit
                """,
                {"limit": limit},
            )
            return [dict(record) for record in rows]
    except Exception:
        return []


@router.post("/api/ai/link")
def ai_link(req: AIAnalysisRequest):
    ensure_platform_tables()

    # Detection-id path: numeric entity_id → deterministic candidate generation.
    entity_id = (req.entity_id or "").strip()
    if entity_id.isdigit():
        from main import generate_candidate_links_for_detection
        candidates = generate_candidate_links_for_detection(int(entity_id))
        return {
            "links": candidates,
            "source": "detection_candidate_links",
            "status": "review_required",
            "policy": "human_approval_required",
        }

    # Free-text path: ask the LLM to rank against a slice of the target graph.
    targets = _fetch_target_summaries()
    if not targets:
        return {"links": [], "status": "no_targets", "policy": "human_approval_required"}

    target_lines = "\n".join(
        f"- id={t['id']} name={t.get('name')} type={t.get('type') or '?'} category={t.get('category') or '?'}"
        for t in targets
    )
    system = (
        "You match OSINT mentions to known targets. Return strict JSON: "
        '{"links": [{"target_id": <id from list>, "relationship": '
        '"CANDIDATE_MATCH", "confidence": 0..1, "reason": <short string>}]}. '
        "Only include links you have evidence for. Empty list is fine."
    )
    prompt_text = (
        f"Context: {req.prompt or json.dumps(req.context, default=str)}\n\n"
        f"Known targets:\n{target_lines}"
    )
    try:
        data = get_llm_json(prompt=prompt_text, system=system, max_tokens=600)
    except AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    by_id = {str(t["id"]): t for t in targets}
    links = []
    for item in data.get("links") or []:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("target_id") or "").strip()
        if tid not in by_id:
            continue
        try:
            conf = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        links.append({
            "source": entity_id or "submitted_context",
            "target_id": tid,
            "target_name": by_id[tid].get("name"),
            "relationship": "CANDIDATE_MATCH",
            "confidence": max(0.0, min(1.0, conf)),
            "reason": str(item.get("reason") or "")[:300],
        })
    return {
        "links": links,
        "source": "llm_rank",
        "status": "review_required",
        "policy": "human_approval_required",
    }


@router.post("/api/ai/propose-actions")
def ai_propose_actions(req: AIActionProposalRequest):
    ensure_platform_tables()
    domain = normalize_domain(req.domain, "WORKFLOW")
    title = req.payload.get("title") or f"AI proposal: {req.action_type.replace('_', ' ')}"
    rationale = f"Proposed from analyst prompt: {req.prompt[:500]}"
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO ai_action_proposals (action_type, title, domain, target_id, rationale, sources, payload, confidence, risk_level, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0.62, %s, 'pending_approval')
            RETURNING id, action_type, title, domain, target_id, rationale, sources, payload, confidence, risk_level, status, created_at, updated_at
        """, (
            req.action_type,
            title[:255],
            domain,
            req.target_id,
            rationale,
            json.dumps(req.payload.get("sources", [])),
            json.dumps({**req.payload, "prompt": req.prompt}),
            req.risk_level,
        ))
        proposal = dict(cursor.fetchone())
    record_timeline_event(domain, "ai_action_proposed", proposal["title"], {"proposal_id": proposal["id"]}, entity_id=req.target_id)
    publish_event("ops", {"type": "ai_action_proposed", "proposal": proposal})
    return {"proposal": proposal, "policy": "human_approval_required"}


@router.get("/api/actions/proposals")
def list_action_proposals(status: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500)):
    ensure_platform_tables()
    params: list = []
    where = ""
    if status:
        where = "WHERE status = %s"
        params.append(status)
    params.append(limit)
    with postgis_db.get_cursor() as cursor:
        cursor.execute(f"""
            SELECT id, action_type, title, domain, target_id, rationale, sources, payload,
                   confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
            FROM ai_action_proposals
            {where}
            ORDER BY created_at DESC
            LIMIT %s
        """, params)
        return {"proposals": [dict(row) for row in cursor.fetchall()]}


@router.post("/api/actions/proposals/{proposal_id}/approve")
def approve_action_proposal(proposal_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE ai_action_proposals
            SET status = 'approved', approved_by = 'local_user', updated_at = NOW()
            WHERE id = %s AND status = 'pending_approval'
            RETURNING id, action_type, title, domain, target_id, rationale, sources, payload,
                      confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
        """, (proposal_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Pending proposal not found")
        proposal = dict(row)
    record_timeline_event(proposal.get("domain") or "WORKFLOW", "ai_action_approved", proposal["title"], {"proposal_id": proposal_id}, entity_id=proposal.get("target_id"))
    publish_event("ops", {"type": "ai_action_approved", "proposal": proposal})
    return {"proposal": proposal}


@router.post("/api/actions/proposals/{proposal_id}/execute")
def execute_action_proposal(proposal_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, action_type, title, domain, target_id, rationale, sources, payload,
                   confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
            FROM ai_action_proposals
            WHERE id = %s
        """, (proposal_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal = dict(row)
    if proposal["status"] != "approved":
        raise HTTPException(status_code=409, detail="Proposal must be approved before execution")
    if proposal["risk_level"] not in {"low", "medium"}:
        raise HTTPException(status_code=403, detail="High-risk proposals require an external allowlisted connector")

    payload = proposal.get("payload") or {}
    result = {"executed": True, "mode": "internal_only"}
    if proposal["action_type"] == "queue_analytic":
        from routers.analytics import run_viewshed
        result["analytic"] = run_viewshed(AnalyticsRequest(target_id=proposal.get("target_id"), radius_m=payload.get("radius_m", 5000))).get("job")
    elif proposal["action_type"] == "generate_report":
        from reports import create_target_package
        result["report"] = create_target_package(
            proposal.get("target_id"),
            proposal["title"],
            payload.get("sources", []),
            payload,
        )
    elif proposal["action_type"] == "create_requirement":
        from reports import create_collection_requirement
        result["requirement"] = create_collection_requirement(
            proposal.get("target_id"),
            proposal["title"],
            payload.get("description", proposal.get("rationale") or ""),
            payload.get("priority", "Medium"),
            payload.get("aoi", {}),
        )
    else:
        result["message"] = f"Action type '{proposal['action_type']}' has no internal connector."

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE ai_action_proposals
            SET status = 'executed', executed_at = NOW(), result = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, action_type, title, domain, target_id, rationale, sources, payload,
                      confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
        """, (json.dumps(result, default=str), proposal_id))
        executed = dict(cursor.fetchone())
    record_timeline_event(executed.get("domain") or "WORKFLOW", "ai_action_executed", executed["title"], {"proposal_id": proposal_id, "result": result}, entity_id=executed.get("target_id"))
    publish_event("ops", {"type": "ai_action_executed", "proposal": executed})
    return {"proposal": executed, "result": result}
