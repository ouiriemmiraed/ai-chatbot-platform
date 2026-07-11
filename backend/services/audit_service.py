"""
Audit logging service — writes a record for every significant security/access event.
Never raises exceptions so a logging failure never breaks the calling endpoint.
"""

import logging
from typing import Optional
from datetime import datetime
from core.database import get_database

logger = logging.getLogger(__name__)


async def log_action(
    action: str,
    performed_by: str,
    chatbot_id: Optional[str] = None,
    target_user: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """
    Insert an audit log entry into the `audit_logs` collection.

    Parameters
    ----------
    action       : dot-namespaced string, e.g. "permission:granted", "chatbot:deleted"
    performed_by : email of the authenticated user who triggered the event
    chatbot_id   : MongoDB ObjectId string of the involved chatbot (if any)
    target_user  : email of the user being affected (for permission actions)
    details      : arbitrary key-value dict to attach to the log entry
    ip_address   : client IP extracted from the request
    """
    try:
        db = get_database()
        await db.audit_logs.insert_one({
            "action": action,
            "performed_by": performed_by,
            "chatbot_id": chatbot_id,
            "target_user": target_user,
            "details": details or {},
            "ip_address": ip_address,
            "timestamp": datetime.utcnow(),
        })
    except Exception as exc:
        logger.error(f"[audit_service] Failed to write log entry (action={action}): {exc}")
