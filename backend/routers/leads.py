from fastapi import APIRouter, HTTPException, Depends, Request
from models import User, LeadCreate, LeadStatusUpdate
from core.auth import get_current_user
from core.database import get_database
from core.csrf_protection import validate_csrf_token
from routers.chat import _is_origin_allowed, ADMIN_TRUSTED_ORIGINS
from bson import ObjectId
from datetime import datetime
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["Leads"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/submit")
@limiter.limit("20/minute")
async def submit_lead(request: Request, lead: LeadCreate):
    """Public endpoint called by the widget when a visitor provides their email."""
    db = get_database()

    if not ObjectId.is_valid(lead.chatbot_id):
        raise HTTPException(status_code=400, detail="Invalid chatbot ID")

    chatbot = await db.chatbot_configs.find_one({"_id": ObjectId(lead.chatbot_id)})
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")

    # ── Domain whitelist ───────────────────────────────────────────────────────
    # Same tenant-isolation gate as /chat/stream and /chatbot/public/{id}: a lead
    # may only be submitted from a domain the owner authorised for this chatbot,
    # so a third-party site cannot poison the lead inbox / harvest chatbot ids.
    allowed_domains = chatbot.get("allowed_domains", [])
    if allowed_domains:
        origin = request.headers.get("origin", "")
        referer = request.headers.get("referer", "")
        is_dev_request = origin in ADMIN_TRUSTED_ORIGINS
        if not is_dev_request and not _is_origin_allowed(origin, referer, allowed_domains):
            client_ip = request.client.host if request.client else "unknown"
            logger.warning(
                f"[lead-domain-block] chatbot={lead.chatbot_id} "
                f"origin={origin!r} referer={referer!r} "
                f"allowed={allowed_domains} ip={client_ip}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Domain not authorized. Lead capture is only allowed on: {', '.join(allowed_domains)}",
            )

    email_lower = str(lead.email).lower()

    # Avoid duplicate leads for the same chatbot
    existing = await db.leads.find_one({"chatbot_id": lead.chatbot_id, "email": email_lower})
    if existing:
        return {"message": "Lead already registered", "id": str(existing["_id"])}

    lead_doc = lead.model_dump()
    lead_doc["email"] = email_lower
    lead_doc["status"] = "new"
    lead_doc["created_at"] = datetime.utcnow()

    result = await db.leads.insert_one(lead_doc)
    return {"message": "Lead captured successfully", "id": str(result.inserted_id)}


@router.get("/")
async def list_leads(
    chatbot_id: str = None,
    status: str = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user)
):
    """List all leads for the current user's chatbots."""
    db = get_database()

    # Collect chatbot IDs the user owns
    user_chatbots = []
    async for config in db.chatbot_configs.find({"user_id": str(current_user.id)}, {"_id": 1}):
        user_chatbots.append(str(config["_id"]))

    # Also include chatbots shared with admin-level permission
    async for perm in db.chatbot_permissions.find({
        "user_email": current_user.email,
        "permission_level": {"$in": ["admin"]}
    }):
        if perm["chatbot_id"] not in user_chatbots:
            user_chatbots.append(perm["chatbot_id"])

    if not user_chatbots:
        return {"leads": [], "total": 0}

    query = {"chatbot_id": {"$in": user_chatbots}}
    if chatbot_id and chatbot_id in user_chatbots:
        query["chatbot_id"] = chatbot_id
    if status:
        query["status"] = status

    total = await db.leads.count_documents(query)
    leads = []
    async for lead in db.leads.find(query).sort("created_at", -1).skip(skip).limit(limit):
        lead["_id"] = str(lead["_id"])
        if ObjectId.is_valid(lead.get("chatbot_id", "")):
            chatbot = await db.chatbot_configs.find_one(
                {"_id": ObjectId(lead["chatbot_id"])}, {"name": 1}
            )
            lead["chatbot_name"] = chatbot["name"] if chatbot else "Unknown"
        leads.append(lead)

    return {"leads": leads, "total": total}


@router.put("/{lead_id}")
async def update_lead(
    lead_id: str,
    update: LeadStatusUpdate,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """Update a lead's status or notes."""
    db = get_database()

    if not ObjectId.is_valid(lead_id):
        raise HTTPException(status_code=400, detail="Invalid lead ID")

    lead = await db.leads.find_one({"_id": ObjectId(lead_id)})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Ownership check
    chatbot = await db.chatbot_configs.find_one({
        "_id": ObjectId(lead["chatbot_id"]),
        "user_id": str(current_user.id)
    })
    if not chatbot:
        # Not the owner — allow if the user has admin-level permission on this
        # chatbot, consistent with which leads list_leads exposes.
        perm = await db.chatbot_permissions.find_one({
            "chatbot_id": lead["chatbot_id"],
            "user_email": current_user.email,
            "permission_level": {"$in": ["admin"]},
        })
        if not perm:
            raise HTTPException(status_code=403, detail="Access denied")

    await db.leads.update_one(
        {"_id": ObjectId(lead_id)},
        {"$set": {"status": update.status, "notes": update.notes}}
    )
    return {"message": "Lead updated successfully"}


@router.delete("/{lead_id}")
async def delete_lead(
    lead_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """Delete a lead."""
    db = get_database()

    if not ObjectId.is_valid(lead_id):
        raise HTTPException(status_code=400, detail="Invalid lead ID")

    lead = await db.leads.find_one({"_id": ObjectId(lead_id)})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    chatbot = await db.chatbot_configs.find_one({
        "_id": ObjectId(lead["chatbot_id"]),
        "user_id": str(current_user.id)
    })
    if not chatbot:
        # Not the owner — allow if the user has admin-level permission on this
        # chatbot, consistent with which leads list_leads exposes.
        perm = await db.chatbot_permissions.find_one({
            "chatbot_id": lead["chatbot_id"],
            "user_email": current_user.email,
            "permission_level": {"$in": ["admin"]},
        })
        if not perm:
            raise HTTPException(status_code=403, detail="Access denied")

    await db.leads.delete_one({"_id": ObjectId(lead_id)})
    return {"message": "Lead deleted successfully"}
