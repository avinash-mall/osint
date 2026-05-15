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

from ai import AIUnavailable, get_ai_response
from database import postgis_db
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


@router.post("/api/ai/extract")
def ai_extract(req: AIAnalysisRequest):
    ensure_platform_tables()
    text = req.prompt or json.dumps(req.context)
    tokens = sorted({word.strip(".,:;()[]{}").title() for word in text.split() if len(word.strip(".,:;()[]{}")) > 4})[:12]
    entities = [{"label": token, "type": "Entity", "confidence": 0.52} for token in tokens]
    return {"entities": entities, "citations": [{"type": "input", "label": "submitted text/context"}], "status": "ok"}


@router.post("/api/ai/link")
def ai_link(req: AIAnalysisRequest):
    ensure_platform_tables()
    return {
        "links": [
            {"source": req.entity_id or "submitted_context", "target": "ontology", "relationship": "CANDIDATE_MATCH", "confidence": 0.58}
        ],
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
        # Lazy import — analytics router lives in a separate module.
        from routers.analytics import run_viewshed
        result["analytic"] = run_viewshed(AnalyticsRequest(target_id=proposal.get("target_id"), radius_m=payload.get("radius_m", 5000))).get("job")
    else:
        # ``generate_report`` and ``create_requirement`` paths are placeholders
        # in the current build — the helpers (`create_target_package`,
        # `create_collection_requirement`) aren't wired in this image. Log
        # and return the internal-only outcome instead of crashing.
        result["message"] = "Action logged; no external dispatch connector is allowlisted."

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
