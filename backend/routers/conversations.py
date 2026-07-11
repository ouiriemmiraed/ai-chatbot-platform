from fastapi import APIRouter, HTTPException, Depends, Request, Query
from typing import List, Optional
from models import User, MessageFeedback
from core.auth import get_current_user
from core.database import get_database
from core.csrf_protection import validate_csrf_token
from routers.permissions import check_permission, get_user_chatbots
from bson import ObjectId
from slowapi import Limiter
from slowapi.util import get_remote_address
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversations"])
limiter = Limiter(key_func=get_remote_address)


@router.get("/feedback-stats/{chatbot_id}")
async def get_feedback_stats(
    chatbot_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get aggregated feedback statistics for a chatbot."""
    from routers.permissions import check_permission
    await check_permission(chatbot_id, current_user.email)

    db = get_database()
    if not ObjectId.is_valid(chatbot_id):
        raise HTTPException(status_code=400, detail="Invalid chatbot ID")

    pipeline = [
        {"$match": {"chatbot_id": chatbot_id}},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "positive": {"$sum": {"$cond": [{"$eq": ["$rating", 1]}, 1, 0]}},
            "negative": {"$sum": {"$cond": [{"$eq": ["$rating", -1]}, 1, 0]}},
        }}
    ]
    result = await db.message_feedback.aggregate(pipeline).to_list(1)
    if result:
        stats = result[0]
        total = stats["total"]
        positive = stats["positive"]
        negative = stats["negative"]
        satisfaction_rate = round((positive / total) * 100, 1) if total > 0 else 0
        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "satisfaction_rate": satisfaction_rate,
        }
    return {"total": 0, "positive": 0, "negative": 0, "satisfaction_rate": 0}


@router.post("/{conversation_id}/feedback")
@limiter.limit("20/minute")
async def submit_feedback(
    conversation_id: str,
    feedback: MessageFeedback,
    request: Request,
):
    """Submit feedback (up/down vote) for a message.

    Public (the widget calls it), so it is hardened against abuse:
    - rate-limited per IP,
    - the chatbot_id is taken from the stored conversation, NOT from the body
      (an attacker cannot attribute feedback to another tenant's chatbot),
    - message_index must point at a real assistant message,
    - rating is constrained to a thumbs up/down.
    """
    db = get_database()

    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    # Verify conversation exists
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Constrain the rating to thumbs up / down
    if feedback.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="rating must be 1 or -1")

    # Validate message_index against the conversation's actual messages
    message_count = len(conversation.get("messages", []))
    if feedback.message_index < 0 or feedback.message_index >= message_count:
        raise HTTPException(status_code=400, detail="Invalid message_index")

    # Save feedback — force trusted fields from the server side
    feedback_dict = feedback.model_dump(exclude={"id"})
    feedback_dict["conversation_id"] = conversation_id
    feedback_dict["chatbot_id"] = conversation.get("chatbot_id")  # bind to real owner, ignore body
    feedback_dict["created_at"] = datetime.utcnow()

    # Upsert feedback for this specific message in this conversation
    await db.message_feedback.update_one(
        {
            "conversation_id": conversation_id,
            "message_index": feedback.message_index
        },
        {"$set": feedback_dict},
        upsert=True
    )

    return {"message": "Feedback submitted successfully"}

@router.get("/")
@limiter.limit("30/minute")
async def get_conversations(
    request: Request,
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of conversations to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max conversations to return"),
    chatbot_id: Optional[str] = Query(None, description="Filter by chatbot ID"),
):
    """
    Get conversations from all chatbots the user has access to (owned + shared).
    """
    db = get_database()
    
    # Get all chatbot IDs the user has access to
    chatbot_ids = await get_user_chatbots(current_user.email)
    
    # Build query filter
    query_filter = {"chatbot_id": {"$in": chatbot_ids}}
    if chatbot_id:
        if chatbot_id not in chatbot_ids:
            raise HTTPException(status_code=403, detail="Access denied to this chatbot")
        query_filter = {"chatbot_id": chatbot_id}
    
    # Get total count
    total = await db.conversations.count_documents(query_filter)
    
    # Get paginated conversations
    conversations = []
    cursor = db.conversations.find(query_filter).sort("updated_at", -1).skip(skip).limit(limit)
    async for conv in cursor:
        conv["_id"] = str(conv["_id"])
        # Only include message count, not full messages
        conv["message_count"] = len(conv.get("messages", []))
        conv.pop("messages", None)
        conversations.append(conv)
    
    return {
        "conversations": conversations,
        "total": total,
        "skip": skip,
        "limit": limit,
    }

@router.get("/analytics-overview")
async def get_analytics_overview(
    current_user: User = Depends(get_current_user),
    days: int = Query(30, ge=7, le=365),
    chatbot_id: Optional[str] = Query(None),
):
    """Get analytics overview: volume by day, peak hours, per-chatbot stats."""
    db = get_database()

    chatbot_ids = await get_user_chatbots(current_user.email)

    if chatbot_id:
        if not ObjectId.is_valid(chatbot_id):
            raise HTTPException(status_code=400, detail="Invalid chatbot ID")
        if chatbot_id not in chatbot_ids:
            raise HTTPException(status_code=403, detail="Access denied")
        filter_ids = [chatbot_id]
    else:
        filter_ids = chatbot_ids

    from_date = datetime.utcnow() - timedelta(days=days)
    match_stage = {"chatbot_id": {"$in": filter_ids}, "created_at": {"$gte": from_date}}

    # Conversations grouped by calendar day
    by_day_raw = await db.conversations.aggregate([
        {"$match": match_stage},
        {"$group": {
            "_id": {
                "year": {"$year": "$created_at"},
                "month": {"$month": "$created_at"},
                "day": {"$dayOfMonth": "$created_at"},
            },
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id.year": 1, "_id.month": 1, "_id.day": 1}}
    ]).to_list(None)
    conversations_by_day = [
        {"date": f"{r['_id']['year']}-{r['_id']['month']:02d}-{r['_id']['day']:02d}", "count": r["count"]}
        for r in by_day_raw
    ]

    # Peak hours (0-23)
    peak_hours_raw = await db.conversations.aggregate([
        {"$match": match_stage},
        {"$group": {"_id": {"$hour": "$created_at"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]).to_list(None)
    peak_hours = [{"hour": r["_id"], "count": r["count"]} for r in peak_hours_raw]

    # Per-chatbot stats
    per_chatbot_raw = await db.conversations.aggregate([
        {"$match": match_stage},
        {"$group": {
            "_id": "$chatbot_id",
            "conversations": {"$sum": 1},
            "total_messages": {"$sum": {"$size": {"$ifNull": ["$messages", []]}}},
        }}
    ]).to_list(None)
    per_chatbot = [
        {
            "chatbot_id": r["_id"],
            "conversations": r["conversations"],
            "total_messages": r["total_messages"],
            "avg_messages": round(r["total_messages"] / r["conversations"], 1) if r["conversations"] > 0 else 0,
        }
        for r in per_chatbot_raw
    ]

    total_conversations = sum(r["conversations"] for r in per_chatbot)
    total_messages = sum(r["total_messages"] for r in per_chatbot)

    return {
        "days": days,
        "total_conversations": total_conversations,
        "total_messages": total_messages,
        "conversations_by_day": conversations_by_day,
        "peak_hours": peak_hours,
        "per_chatbot": per_chatbot,
    }

@router.get("/{conversation_id}", response_model=dict)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific conversation (if user has access to the chatbot).
    """
    db = get_database()
    
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Check if user has access to the chatbot
    await check_permission(conversation["chatbot_id"], current_user.email)
    
    conversation["_id"] = str(conversation["_id"])
    return conversation

@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """
    Delete a conversation (requires ADMIN permission or owner).
    """
    from models import PermissionLevel
    
    db = get_database()
    
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    
    conversation = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Check if user has admin access to the chatbot
    await check_permission(conversation["chatbot_id"], current_user.email, required_level=PermissionLevel.ADMIN)
    
    await db.conversations.delete_one({"_id": ObjectId(conversation_id)})

    # Cascade: drop data derived from this conversation so deleting it never
    # leaves orphaned tags / quality scores / feedback / cost rows behind.
    # conversation_tags, quality_scores & llm_costs store conversation_id as
    # ObjectId; message_feedback stores it as a string — match each accordingly.
    oid = ObjectId(conversation_id)
    for coll, key in (("conversation_tags", oid), ("quality_scores", oid),
                      ("llm_costs", oid), ("message_feedback", conversation_id)):
        try:
            res = await db[coll].delete_many({"conversation_id": key})
            if res.deleted_count:
                logger.info(f"Cascade delete: removed {res.deleted_count} from {coll} for conversation {conversation_id}")
        except Exception as e:
            logger.warning(f"Cascade delete failed for {coll} (conversation {conversation_id}): {e}")

    return {"message": "Conversation deleted successfully"}


@router.get("/analytics/{chatbot_id}/keywords")
async def get_top_keywords(
    chatbot_id: str,
    current_user: User = Depends(get_current_user),
    limit: int = Query(20, ge=5, le=50, description="Number of top keywords to return"),
    days: int = Query(None, ge=1, le=365, description="Only analyze conversations from the last N days (default: all time)"),
):
    """
    Analyze conversations and extract the most frequently asked keywords/topics.
    Returns top keywords from user messages (not bot responses).
    When ``days`` is provided, only conversations from that window are analyzed
    so the result lines up with the dashboard's date-range selector.
    """
    await check_permission(chatbot_id, current_user.email)
    
    db = get_database()
    if not ObjectId.is_valid(chatbot_id):
        raise HTTPException(status_code=400, detail="Invalid chatbot ID")
    
    # French and English common stop words to filter out
    stop_words = {
        # French
        'le', 'la', 'les', 'un', 'une', 'des', 'du', 'de', 'et', 'ou', 'mais', 'donc',
        'car', 'ni', 'ne', 'pas', 'plus', 'moins', 'très', 'tout', 'tous', 'toute',
        'toutes', 'ce', 'cet', 'cette', 'ces', 'mon', 'ton', 'son', 'ma', 'ta', 'sa',
        'mes', 'tes', 'ses', 'je', 'tu', 'il', 'elle', 'nous', 'vous', 'ils', 'elles',
        'on', 'qui', 'que', 'quoi', 'dont', 'où', 'être', 'avoir', 'faire', 'dire',
        'aller', 'voir', 'savoir', 'pouvoir', 'falloir', 'vouloir', 'venir', 'au',
        'aux', 'pour', 'par', 'dans', 'sur', 'avec', 'sans', 'sous', 'entre', 'vers',
        'chez', 'est', 'sont', 'était', 'été', 'ai', 'as', 'a', 'avons', 'avez', 'ont',
        'peut', 'peuvent', 'puis', 'si', 'oui', 'non', 'comment', 'pourquoi', 'quand',
        'quel', 'quelle', 'quels', 'quelles', 'peux', 'pouvez', 'votre', 'vos', 'leur',
        'leurs', 'notre', 'nos', 'cela', 'ça', 'y', 'en', 'aussi', 'encore', 'déjà',
        # English
        'the', 'a', 'an', 'and', 'or', 'but', 'if', 'then', 'else', 'when', 'at', 'by',
        'for', 'with', 'about', 'against', 'between', 'into', 'through', 'during', 'before',
        'after', 'above', 'below', 'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off',
        'over', 'under', 'again', 'further', 'then', 'once', 'here', 'there', 'all', 'both',
        'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only',
        'own', 'same', 'so', 'than', 'too', 'very', 'can', 'will', 'just', 'should', 'now',
        'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does',
        'did', 'doing', 'would', 'could', 'this', 'that', 'these', 'those', 'i', 'you', 'he',
        'she', 'it', 'we', 'they', 'them', 'their', 'what', 'which', 'who', 'whom', 'whose',
        'why', 'how', 'my', 'your', 'his', 'her', 'its', 'our', 'am', 'want', 'me', 'get',
        'like', 'know', 'think', 'see', 'make', 'go', 'take', 'come', 'give', 'use', 'find',
        # Greetings / politeness (FR) — conversational noise, not topics
        'bonjour', 'bonsoir', 'salut', 'coucou', 'merci', 'svp', 'stp', 'plait', 'plaît',
        'cordialement', 'bienvenue', 'bonne', 'journee', 'journée', 'soiree', 'soirée',
        # Generic question verbs / fillers (FR)
        'proposez', 'propose', 'proposer', 'proposes', 'faites', 'fais', 'vendez', 'vends',
        'vendre', 'vend', 'couvrez', 'couvre', 'couvrir', 'donner', 'donne', 'donnez',
        'avez', 'aimerais', 'voudrais', 'souhaite', 'souhaiterais', 'cherche', 'besoin',
        'dites', 'explique', 'expliquer', 'voila', 'voilà', 'alors', 'juste', 'bien',
        # Greetings / politeness / fillers (EN)
        'hello', 'hi', 'hey', 'thanks', 'thank', 'thx', 'please', 'welcome', 'good',
        'morning', 'offer', 'offers', 'provide', 'provides', 'sell', 'sells', 'gives',
        'explain', 'explains', 'explained', 'ok', 'okay', 'yeah', 'yes',
        # Test/demo noise
        'test', 'tests', 'prod', 'demo', 'phrase',
    }
    
    # Get conversations for this chatbot (optionally scoped to the last N days)
    kw_query = {"chatbot_id": chatbot_id}
    if days:
        kw_query["created_at"] = {"$gte": datetime.utcnow() - timedelta(days=days)}
    conversations = await db.conversations.find(kw_query).to_list(None)
    
    # Extract and count words from user messages
    word_counts = {}
    total_messages = 0
    
    for conv in conversations:
        messages = conv.get("messages", [])
        for msg in messages:
            # Only analyze user messages (stored with role="user", not type)
            if msg.get("role") == "user":
                total_messages += 1
                text = msg.get("content", "").lower()
                
                # Simple tokenization: split by whitespace and punctuation
                import re
                words = re.findall(r'\b[a-zàâäéèêëïîôùûüÿæœç]{3,}\b', text)
                
                for word in words:
                    # Skip stop words and very short words
                    if word not in stop_words and len(word) >= 3:
                        word_counts[word] = word_counts.get(word, 0) + 1
    
    # Sort by frequency and get top N
    sorted_keywords = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    
    # Format response
    keywords = [
        {
            "word": word,
            "count": count,
            "percentage": round((count / total_messages * 100), 1) if total_messages > 0 else 0
        }
        for word, count in sorted_keywords
    ]
    
    return {
        "keywords": keywords,
        "total_messages": total_messages,
        "total_conversations": len(conversations),
        "unique_words": len(word_counts)
    }

