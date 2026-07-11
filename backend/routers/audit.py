"""
Audit log endpoints — only accessible to chatbot owner or admin.
"""

from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from models import User, PermissionLevel
from core.auth import get_current_user
from core.database import get_database
from routers.permissions import check_permission

router = APIRouter(prefix="/audit", tags=["Audit Logs"])


@router.get("/chatbot/{chatbot_id}")
async def get_chatbot_audit_logs(
    chatbot_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    action_filter: Optional[str] = Query(None, description="Filter by action prefix, e.g. 'permission'"),
    current_user: User = Depends(get_current_user),
):
    """
    Return audit logs for a specific chatbot (newest first).
    Requires Admin or Owner permission.
    """
    await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.ADMIN)

    db = get_database()

    query: dict = {"chatbot_id": chatbot_id}
    if action_filter:
        # Allow filtering by action prefix, e.g. "permission" matches "permission:granted"
        import re
        escaped = re.escape(action_filter)
        query["action"] = {"$regex": f"^{escaped}", "$options": "i"}

    total = await db.audit_logs.count_documents(query)

    logs = []
    async for log in (
        db.audit_logs.find(query)
        .sort("timestamp", -1)
        .skip(skip)
        .limit(limit)
    ):
        log["_id"] = str(log["_id"])
        # Stringify timestamp for JSON serialization
        if hasattr(log.get("timestamp"), "isoformat"):
            log["timestamp"] = log["timestamp"].isoformat()
        logs.append(log)

    return {"logs": logs, "total": total, "skip": skip, "limit": limit}
