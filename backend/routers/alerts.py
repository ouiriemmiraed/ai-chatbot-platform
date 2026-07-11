from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime
from bson import ObjectId
from core.database import get_database
from core.auth import get_current_user
from core.csrf_protection import validate_csrf_token
from models import User
from routers.permissions import get_user_chatbots
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

@router.get("/user")
async def get_user_alerts(
    acknowledged: Optional[bool] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user)
):
    """Get all change alerts for the current user across all their chatbots"""
    
    db = get_database()
    
    # Get all chatbot IDs that the user has access to
    chatbot_ids = await get_user_chatbots(current_user.email)
    
    if not chatbot_ids:
        return {
            "total": 0,
            "alerts": []
        }
    
    # Build query
    query = {"chatbot_id": {"$in": chatbot_ids}}
    if acknowledged is not None:
        query["acknowledged"] = acknowledged
    
    # Fetch alerts
    alerts = await db.change_alerts.find(query).sort("created_at", -1).limit(limit).to_list(length=limit)
    
    # Get chatbot names for display
    chatbots = await db.chatbot_configs.find(
        {"_id": {"$in": [ObjectId(cid) for cid in chatbot_ids]}}
    ).to_list(length=None)
    chatbots_dict = {str(cb["_id"]): cb for cb in chatbots}
    
    return {
        "total": len(alerts),
        "alerts": [
            {
                "id": str(a["_id"]),
                "page_id": a["page_id"],
                "url": a["url"],
                "chatbot_id": a["chatbot_id"],
                "chatbot_name": chatbots_dict.get(a["chatbot_id"], {}).get("name", "Unknown"),
                "alert_type": a["alert_type"],
                "severity": a["severity"],
                "change_magnitude": a.get("change_magnitude"),
                "change_summary": a.get("change_summary"),
                "created_at": a["created_at"],
                "sent_at": a.get("sent_at"),
                "delivery_status": a.get("delivery_status"),
                "acknowledged": a.get("acknowledged", False),
                "acknowledged_by": a.get("acknowledged_by")
            }
            for a in alerts
        ]
    }

@router.post("/{alert_id}/acknowledge")
async def acknowledge_user_alert(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Mark an alert as acknowledged (user must have access to the chatbot)"""
    
    db = get_database()
    
    # Get the alert
    if not ObjectId.is_valid(alert_id):
        raise HTTPException(status_code=400, detail="Invalid alert ID")
    alert = await db.change_alerts.find_one({"_id": ObjectId(alert_id)})
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    # Verify user has access to this chatbot
    chatbot_ids = await get_user_chatbots(current_user.email)
    
    if alert["chatbot_id"] not in chatbot_ids:
        raise HTTPException(status_code=403, detail="You don't have access to this alert")
    
    # Update the alert
    result = await db.change_alerts.update_one(
        {"_id": ObjectId(alert_id)},
        {"$set": {
            "acknowledged": True,
            "acknowledged_by": current_user.email,
            "acknowledged_at": datetime.utcnow()
        }}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    return {"success": True}
