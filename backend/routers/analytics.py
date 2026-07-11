"""Sprint D analytics — endpoints powering the owner dashboard.

Every endpoint takes `chatbot_id` + optional `days` (default 30) and runs
async aggregations against the new collections (conversation_tags,
quality_scores, llm_costs, content_gaps) plus the existing conversations
collection.

Authorisation: every endpoint reuses `check_permission(...)` so only users
with at least VIEWER level on the chatbot can pull its analytics.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Depends

from core.database import get_database
from core.csrf_protection import validate_csrf_token
from routers.auth import get_current_user
from routers.permissions import check_permission
from models import PermissionLevel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["Analytics"])


# ── helpers ──────────────────────────────────────────────────────────────────
def _validate_id_and_days(chatbot_id: str, days: int) -> tuple[ObjectId, datetime]:
    if not ObjectId.is_valid(chatbot_id):
        raise HTTPException(status_code=400, detail="Invalid chatbot id")
    days = max(1, min(int(days), 365))
    cutoff = datetime.utcnow() - timedelta(days=days)
    return ObjectId(chatbot_id), cutoff


async def _ensure_view_perm(chatbot_id: str, user_email: str) -> None:
    await check_permission(chatbot_id, user_email, required_level=PermissionLevel.VIEWER)


# ── /dashboard summary ───────────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/dashboard")
async def owner_dashboard(
    chatbot_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user=Depends(get_current_user),
):
    """One-shot summary used to render the dashboard hero metrics."""
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, cutoff = _validate_id_and_days(chatbot_id, days)
    db = get_database()

    # #4: anchor every metric on the conversation's creation time. Conversations
    # are filtered by created_at; the tag/quality breakdowns by the denormalised
    # conv_created_at (written by the classifier/judge) so the total stays
    # consistent with the breakdowns WITHOUT an $in over the conversation set
    # (scales to large histories — indexed on (chatbot_id, conv_created_at)).
    win = {"chatbot_id": chatbot_id, "conv_created_at": {"$gte": cutoff}}
    total_conv = await db.conversations.count_documents(
        {"chatbot_id": chatbot_id, "created_at": {"$gte": cutoff}}
    )

    # Outcome breakdown from conversation_tags
    outcomes_cursor = db.conversation_tags.aggregate([
        {"$match": win},
        {"$group": {"_id": "$outcome", "count": {"$sum": 1}}},
    ])
    outcomes = {doc["_id"]: doc["count"] async for doc in outcomes_cursor}

    # Sentiment breakdown
    sentiment_cursor = db.conversation_tags.aggregate([
        {"$match": win},
        {"$group": {"_id": "$sentiment", "count": {"$sum": 1}}},
    ])
    sentiments = {doc["_id"]: doc["count"] async for doc in sentiment_cursor}

    # Lead capture (high + medium quality)
    leads_total = await db.conversation_tags.count_documents({
        **win, "is_lead_quality": {"$in": ["high", "medium"]},
    })

    # Average quality score
    quality_cursor = db.quality_scores.aggregate([
        {"$match": win},
        {"$group": {
            "_id": None,
            "avg_aggregate": {"$avg": "$aggregate"},
            "avg_groundedness": {"$avg": "$groundedness"},
            "high_hallucination": {"$sum": {"$cond": [{"$eq": ["$hallucination_risk", "high"]}, 1, 0]}},
            "n": {"$sum": 1},
        }},
    ])
    quality_doc = await quality_cursor.to_list(1)
    quality = quality_doc[0] if quality_doc else {
        "avg_aggregate": None, "avg_groundedness": None, "high_hallucination": 0, "n": 0,
    }
    quality.pop("_id", None)

    # Cost over the window (llm_costs.timestamp ≈ turn time, indexed).
    cost_cursor = db.llm_costs.aggregate([
        {"$match": {"chatbot_id": chatbot_id, "timestamp": {"$gte": cutoff}}},
        {"$group": {
            "_id": None,
            "total_cost_usd": {"$sum": "$cost_usd"},
            "tokens_in": {"$sum": "$tokens_in"},
            "tokens_out": {"$sum": "$tokens_out"},
        }},
    ])
    cost_doc = await cost_cursor.to_list(1)
    cost = cost_doc[0] if cost_doc else {"total_cost_usd": 0, "tokens_in": 0, "tokens_out": 0}
    cost.pop("_id", None)

    # Open content gaps
    open_gaps = await db.content_gaps.count_documents(
        {"chatbot_id": chatbot_id, "status": "open"}
    )

    return {
        "window_days": days,
        "totals": {
            "conversations": total_conv,
            "leads_captured": leads_total,
            "open_content_gaps": open_gaps,
        },
        "outcomes": outcomes,
        "sentiments": sentiments,
        "quality": quality,
        "cost": cost,
    }


# ── funnel ──────────────────────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/funnel")
async def owner_funnel(
    chatbot_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user=Depends(get_current_user),
):
    """Visitor → engaged → email captured → high-intent funnel.

    A funnel must be monotonic (each stage ⊆ the previous). To guarantee that,
    the lead-quality stages are computed as subsets of the *engaged* conversation
    set rather than from conversation_tags independently — otherwise a 1-message
    lead-quality conversation could make email_captured > engaged.

    "engaged"        = conversation has at least 2 user messages.
    "email captured" = engaged conversation tagged is_lead_quality in {medium, high}.
    "high intent"    = engaged conversation tagged is_lead_quality == high.

    (The `leads` collection is keyed by chatbot_id+email and not linked to a
    conversation, so it can't be joined per-conversation; lead capture is read
    from the classifier's is_lead_quality tag.)
    """
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, cutoff = _validate_id_and_days(chatbot_id, days)
    db = get_database()

    # created_at-anchored, consistent with the dashboard totals.
    base = {"chatbot_id": chatbot_id, "created_at": {"$gte": cutoff}}

    # 1. Total visitors = total conversations
    visitors = await db.conversations.count_documents(base)

    # 2. Engaged: conversations with ≥ 2 user messages. Keep the ids so the later
    #    stages are strict subsets (guarantees a monotonic funnel).
    engaged_cursor = db.conversations.aggregate([
        {"$match": base},
        {"$project": {
            "user_msgs": {"$size": {"$filter": {
                "input": {"$ifNull": ["$messages", []]},
                "as": "m", "cond": {"$eq": ["$$m.role", "user"]},
            }}},
        }},
        {"$match": {"user_msgs": {"$gte": 2}}},
    ])
    engaged_ids = [doc["_id"] async for doc in engaged_cursor]
    engaged = len(engaged_ids)

    # 3 & 4. Lead-quality stages restricted to engaged conversations.
    captured = high = 0
    if engaged_ids:
        captured = await db.conversation_tags.count_documents({
            "conversation_id": {"$in": engaged_ids},
            "is_lead_quality": {"$in": ["high", "medium"]},
        })
        high = await db.conversation_tags.count_documents({
            "conversation_id": {"$in": engaged_ids},
            "is_lead_quality": "high",
        })

    return {
        "stages": [
            {"name": "visitors", "count": visitors},
            {"name": "engaged", "count": engaged},
            {"name": "email_captured", "count": captured},
            {"name": "high_intent", "count": high},
        ],
    }


# ── top questions ────────────────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/top-questions")
async def owner_top_questions(
    chatbot_id: str,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    only_unanswered: bool = False,
    current_user=Depends(get_current_user),
):
    """Returns the most frequent topic_keywords with sample questions."""
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, cutoff = _validate_id_and_days(chatbot_id, days)
    db = get_database()

    # Anchor on conv_created_at (when the conversation happened), consistent with
    # the dashboard/funnel — not classified_at (when the classifier later ran).
    match: Dict[str, Any] = {"chatbot_id": chatbot_id, "conv_created_at": {"$gte": cutoff}}
    if only_unanswered:
        match["outcome"] = "unanswered"

    cursor = db.conversation_tags.aggregate([
        {"$match": match},
        {"$unwind": "$topic_keywords"},
        {"$group": {
            "_id": "$topic_keywords",
            "count": {"$sum": 1},
            "sample_summaries": {"$addToSet": "$summary"},
            "conv_ids": {"$addToSet": "$conversation_id"},
        }},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ])
    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        rows.append({
            "keyword": doc["_id"],
            "count": doc["count"],
            "sample_summary": (doc["sample_summaries"] or [""])[0],
            "conversation_ids": [str(c) for c in (doc.get("conv_ids") or [])][:5],
        })
    return {"items": rows}


# ── heatmap ──────────────────────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/heatmap")
async def owner_heatmap(
    chatbot_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user=Depends(get_current_user),
):
    """7×24 grid of conversation counts. Rows = weekday (Mon=1..Sun=7),
    cols = hour (0..23). Mongo $dayOfWeek returns 1..7 with Sunday=1, so we
    remap to Monday=0 client-side. Times are UTC."""
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, cutoff = _validate_id_and_days(chatbot_id, days)
    db = get_database()

    cursor = db.conversations.aggregate([
        {"$match": {"chatbot_id": chatbot_id, "updated_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": {
                "dow": {"$dayOfWeek": "$updated_at"},
                "hour": {"$hour": "$updated_at"},
            },
            "count": {"$sum": 1},
        }},
    ])

    grid = [[0] * 24 for _ in range(7)]
    async for doc in cursor:
        dow_mongo = doc["_id"]["dow"]  # 1=Sun..7=Sat
        # remap: Mon=0, Tue=1, ..., Sun=6
        dow = (dow_mongo + 5) % 7
        hour = doc["_id"]["hour"]
        grid[dow][hour] = doc["count"]

    return {"grid": grid, "labels": {
        "rows": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "cols": [f"{h:02d}h" for h in range(24)],
    }}


# ── quality trend (sparkline) ────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/quality-trend")
async def owner_quality_trend(
    chatbot_id: str,
    days: int = Query(7, ge=1, le=90),
    current_user=Depends(get_current_user),
):
    """Daily average quality score over the window — sparkline source."""
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, cutoff = _validate_id_and_days(chatbot_id, days)
    db = get_database()

    cursor = db.quality_scores.aggregate([
        {"$match": {"chatbot_id": chatbot_id, "scored_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scored_at"}},
            "avg_aggregate": {"$avg": "$aggregate"},
            "n": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ])
    points: List[Dict[str, Any]] = []
    async for doc in cursor:
        points.append({
            "date": doc["_id"],
            "score": round(doc["avg_aggregate"] or 0, 3),
            "samples": doc["n"],
        })
    return {"points": points}


# ── content gaps ─────────────────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/content-gaps")
async def owner_content_gaps(
    chatbot_id: str,
    current_user=Depends(get_current_user),
):
    """All open content-gap clusters for this chatbot."""
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, _ = _validate_id_and_days(chatbot_id, 30)
    db = get_database()

    cursor = db.content_gaps.find(
        {"chatbot_id": chatbot_id, "status": "open"},
        {"_id": 0, "cluster_id": 1, "question_count": 1,
         "example_questions": 1, "suggested_topic": 1, "detected_at": 1},
    ).sort("question_count", -1)
    items = await cursor.to_list(50)
    return {"items": items}


@router.post("/owner/{chatbot_id}/content-gaps/{cluster_id}/{action}")
async def owner_content_gap_action(
    chatbot_id: str,
    cluster_id: str,
    action: str,
    current_user=Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),  # CSRF protection
):
    """Mark a cluster as `addressed` or `ignored` (writes to status)."""
    await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.EDITOR)
    if action not in ("addressed", "ignored"):
        raise HTTPException(status_code=400, detail="invalid action")
    db = get_database()
    res = await db.content_gaps.update_one(
        {"chatbot_id": chatbot_id, "cluster_id": cluster_id},
        {"$set": {"status": action, "actioned_at": datetime.utcnow()}},
    )
    return {"updated": res.modified_count}


# ── cost breakdown ───────────────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/cost")
async def owner_cost(
    chatbot_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user=Depends(get_current_user),
):
    """Daily LLM spend + per-provider breakdown, plus tokens."""
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, cutoff = _validate_id_and_days(chatbot_id, days)
    db = get_database()

    daily_cursor = db.llm_costs.aggregate([
        {"$match": {"chatbot_id": chatbot_id, "timestamp": {"$gte": cutoff}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "cost_usd": {"$sum": "$cost_usd"},
            "tokens_in": {"$sum": "$tokens_in"},
            "tokens_out": {"$sum": "$tokens_out"},
        }},
        {"$sort": {"_id": 1}},
    ])
    daily = []
    async for doc in daily_cursor:
        daily.append({
            "date": doc["_id"],
            "cost_usd": round(doc["cost_usd"] or 0, 6),
            "tokens_in": doc["tokens_in"] or 0,
            "tokens_out": doc["tokens_out"] or 0,
        })

    by_provider_cursor = db.llm_costs.aggregate([
        {"$match": {"chatbot_id": chatbot_id, "timestamp": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$provider",
            "cost_usd": {"$sum": "$cost_usd"},
            "calls": {"$sum": 1},
            "tokens_in": {"$sum": "$tokens_in"},
            "tokens_out": {"$sum": "$tokens_out"},
        }},
        {"$sort": {"cost_usd": -1}},
    ])
    by_provider = []
    async for doc in by_provider_cursor:
        by_provider.append({
            "provider": doc["_id"] or "unknown",
            "cost_usd": round(doc["cost_usd"] or 0, 6),
            "calls": doc["calls"],
            "tokens_in": doc["tokens_in"] or 0,
            "tokens_out": doc["tokens_out"] or 0,
        })

    return {"daily": daily, "by_provider": by_provider}


# ── anomalies banner ─────────────────────────────────────────────────────────
@router.get("/owner/{chatbot_id}/anomalies")
async def owner_anomalies(
    chatbot_id: str,
    current_user=Depends(get_current_user),
):
    """Detect "things the owner should know" right now. Cheap and synchronous;
    safe to call on every dashboard load."""
    await _ensure_view_perm(chatbot_id, current_user.email)
    _, today_cutoff = _validate_id_and_days(chatbot_id, 1)
    _, week_cutoff = _validate_id_and_days(chatbot_id, 7)
    db = get_database()

    alerts: List[Dict[str, Any]] = []

    # 1. Refusal rate today vs the prior 6 days (anchored on conv_created_at so
    #    "today" means conversations that happened today, not ones classified today)
    today_total = await db.conversation_tags.count_documents(
        {"chatbot_id": chatbot_id, "conv_created_at": {"$gte": today_cutoff}}
    )
    today_unanswered = await db.conversation_tags.count_documents({
        "chatbot_id": chatbot_id, "conv_created_at": {"$gte": today_cutoff},
        "outcome": "unanswered",
    })
    if today_total >= 5:
        ratio = today_unanswered / today_total
        if ratio >= 0.20:
            alerts.append({
                "level": "warning",
                "code": "high_refusal_rate",
                "message": f"Le taux de refus aujourd'hui est de {round(ratio*100)} % "
                           f"({today_unanswered}/{today_total}). Voir les gaps de contenu.",
            })

    # 2. Hallucination spike (anchored on conv_created_at for window consistency)
    high_hall = await db.quality_scores.count_documents({
        "chatbot_id": chatbot_id, "conv_created_at": {"$gte": today_cutoff},
        "hallucination_risk": "high",
    })
    if high_hall >= 3:
        alerts.append({
            "level": "danger",
            "code": "hallucination_spike",
            "message": f"{high_hall} réponses à risque d'hallucination élevé aujourd'hui. À vérifier.",
        })

    # 3. Cost spike: today > 1.5× the 7-day daily average
    pipeline = [
        {"$match": {"chatbot_id": chatbot_id, "timestamp": {"$gte": week_cutoff}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "cost": {"$sum": "$cost_usd"},
        }},
    ]
    daily_costs = []
    async for doc in db.llm_costs.aggregate(pipeline):
        daily_costs.append((doc["_id"], doc["cost"] or 0))
    if len(daily_costs) >= 4:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        today_cost = next((c for d, c in daily_costs if d == today_str), 0)
        prior = [c for d, c in daily_costs if d != today_str]
        avg = sum(prior) / max(1, len(prior))
        if today_cost > 1.5 * avg and today_cost > 0.05:  # ignore noise <$0.05
            alerts.append({
                "level": "warning",
                "code": "cost_spike",
                "message": f"Dépense LLM aujourd'hui ({round(today_cost,2)} $) supérieure "
                           f"à 150 % de la moyenne 7 j ({round(avg,2)} $).",
            })

    # 4. Open content gaps
    gaps = await db.content_gaps.count_documents(
        {"chatbot_id": chatbot_id, "status": "open"}
    )
    if gaps:
        alerts.append({
            "level": "info",
            "code": "open_content_gaps",
            "message": f"{gaps} gap(s) de contenu détectés cette semaine — la KB peut être étoffée.",
        })

    return {"alerts": alerts}
