from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from datetime import datetime
from bson import ObjectId
from core.database import get_database
from core.auth import get_current_user
from core.csrf_protection import validate_csrf_token
from models import User
import fnmatch
import re
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


def _oid(value: str, label: str = "ID") -> ObjectId:
    """Validate a 24-hex id and return an ObjectId, or raise 400.

    Prevents bson.errors.InvalidId from bubbling up as an unhandled 500 (DoS /
    error-oracle) when a malformed path/body id is supplied.
    """
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return ObjectId(value)

router = APIRouter(prefix="/api/chatbots", tags=["pages"])

# ===== Request Models =====

class UpdatePagePolicyRequest(BaseModel):
    strategy: Optional[str] = None  # auto, manual, scheduled, never, conditional
    is_pinned: Optional[bool] = None
    pin_reason: Optional[str] = None
    custom_interval_hours: Optional[int] = None
    priority: Optional[int] = None
    check_frequency: Optional[str] = None

class BulkUpdatePagesRequest(BaseModel):
    page_ids: List[str]
    action: str  # pin, unpin, set_priority, set_strategy, delete
    params: Optional[dict] = {}

class AddExclusionPatternRequest(BaseModel):
    pattern: str  # "/blog/old/*"
    reason: Optional[str] = None

# ===== Helper Functions =====

async def check_chatbot_permission(chatbot_id: str, user_email: str, required_levels: List[str]):
    """Check if user has required permission level for chatbot"""
    db = get_database()
    chatbot = await db.chatbot_configs.find_one({"_id": _oid(chatbot_id, "chatbot ID")})
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    
    # Check if owner
    user = await db.users.find_one({"email": user_email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if str(chatbot["user_id"]) == str(user["_id"]):
        return True  # Owner has all permissions
    
    # Check permissions
    permission = await db.chatbot_permissions.find_one({
        "chatbot_id": chatbot_id,
        "user_email": user_email
    })
    
    if not permission or permission["permission_level"] not in required_levels:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions for this action"
        )
    
    return True

def matches_pattern(url: str, patterns: List[str]) -> bool:
    """Check if URL matches any of the patterns"""
    path = urlparse(url).path
    
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(url, pattern):
            return True
    
    return False

# ===== Endpoints =====

@router.get("/{chatbot_id}/pages")
async def list_pages(
    chatbot_id: str,
    strategy: Optional[str] = None,
    is_pinned: Optional[bool] = None,
    priority: Optional[int] = None,
    search: Optional[str] = None,
    sort_by: str = "scraped_at",
    sort_order: str = "desc",
    limit: int = 100,
    skip: int = 0,
    current_user: User = Depends(get_current_user)
):
    """List pages with advanced filters"""
    db = get_database()
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor", "viewer"]
    )
    
    query = {"chatbot_id": chatbot_id}
    
    # Apply filters
    if strategy:
        query["rescrape_policy.strategy"] = strategy
    if is_pinned is not None:
        query["rescrape_policy.is_pinned"] = is_pinned
    if priority is not None:
        query["rescrape_policy.priority"] = priority
    if search:
        # Escape user input so it cannot inject regex metacharacters / cause ReDoS.
        safe_search = re.escape(search[:200])
        query["$or"] = [
            {"title": {"$regex": safe_search, "$options": "i"}},
            {"url": {"$regex": safe_search, "$options": "i"}}
        ]
    
    # Get total count
    total = await db.scraped_pages.count_documents(query)
    
    # Get pages with sorting
    sort_direction = -1 if sort_order == "desc" else 1
    cursor = db.scraped_pages.find(query).sort(sort_by, sort_direction).skip(skip).limit(limit)
    pages = await cursor.to_list(length=limit)
    
    # Convert ObjectId to string
    for page in pages:
        page["_id"] = str(page["_id"])
    
    return {
        "pages": pages,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@router.patch("/{chatbot_id}/pages/{page_id}/policy")
async def update_page_policy(
    chatbot_id: str,
    page_id: str,
    request: UpdatePagePolicyRequest,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Update re-scrape policy for a specific page"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor"]
    )
    
    db = get_database()
    # Check if page exists
    page = await db.scraped_pages.find_one({
        "_id": _oid(page_id, "page ID"),
        "chatbot_id": chatbot_id
    })

    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    # Build update fields
    update_fields = {}
    
    if request.is_pinned is not None:
        update_fields["rescrape_policy.is_pinned"] = request.is_pinned
        if request.is_pinned:
            update_fields["rescrape_policy.pinned_at"] = datetime.utcnow()
            update_fields["rescrape_policy.pinned_by"] = str(current_user.id)
            if request.pin_reason:
                update_fields["rescrape_policy.pin_reason"] = request.pin_reason
    
    if request.strategy:
        update_fields["rescrape_policy.strategy"] = request.strategy
    
    if request.priority is not None:
        update_fields["rescrape_policy.priority"] = request.priority
    
    if request.custom_interval_hours is not None:
        update_fields["rescrape_policy.custom_interval_hours"] = request.custom_interval_hours
    
    if request.check_frequency:
        update_fields["rescrape_policy.check_frequency"] = request.check_frequency
    
    update_fields["rescrape_policy.updated_at"] = datetime.utcnow()
    
    # Update page
    result = await db.scraped_pages.update_one(
        {"_id": _oid(page_id, "page ID"), "chatbot_id": chatbot_id},
        {"$set": update_fields}
    )
    
    # Log audit event
    await db.audit_logs.insert_one({
        "action": "page:policy_updated",
        "performed_by": current_user.email,
        "chatbot_id": chatbot_id,
        "details": {
            "page_id": page_id,
            "page_url": page.get("url"),
            "changes": update_fields
        },
        "timestamp": datetime.utcnow()
    })
    
    # Update chatbot statistics
    await update_chatbot_stats(chatbot_id)
    
    return {"success": True, "modified": result.modified_count}

@router.post("/{chatbot_id}/pages/bulk-update")
async def bulk_update_pages(
    chatbot_id: str,
    request: BulkUpdatePagesRequest,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Perform bulk action on multiple pages"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor"]
    )
    
    db = get_database()
    page_ids = [_oid(pid, "page ID") for pid in request.page_ids]
    
    result = None
    
    if request.action == "pin":
        result = await db.scraped_pages.update_many(
            {"_id": {"$in": page_ids}, "chatbot_id": chatbot_id},
            {"$set": {
                "rescrape_policy.is_pinned": True,
                "rescrape_policy.pinned_at": datetime.utcnow(),
                "rescrape_policy.pinned_by": str(current_user.id),
                "rescrape_policy.pin_reason": request.params.get("reason"),
                "rescrape_policy.updated_at": datetime.utcnow()
            }}
        )
    
    elif request.action == "unpin":
        result = await db.scraped_pages.update_many(
            {"_id": {"$in": page_ids}, "chatbot_id": chatbot_id},
            {"$set": {
                "rescrape_policy.is_pinned": False,
                "rescrape_policy.updated_at": datetime.utcnow()
            }}
        )
    
    elif request.action == "set_priority":
        priority = request.params.get("priority", 0)
        result = await db.scraped_pages.update_many(
            {"_id": {"$in": page_ids}, "chatbot_id": chatbot_id},
            {"$set": {
                "rescrape_policy.priority": priority,
                "rescrape_policy.updated_at": datetime.utcnow()
            }}
        )
    
    elif request.action == "set_strategy":
        strategy = request.params.get("strategy", "auto")
        result = await db.scraped_pages.update_many(
            {"_id": {"$in": page_ids}, "chatbot_id": chatbot_id},
            {"$set": {
                "rescrape_policy.strategy": strategy,
                "rescrape_policy.updated_at": datetime.utcnow()
            }}
        )
    
    elif request.action == "delete":
        # Get pages to delete
        pages_to_delete = await db.scraped_pages.find({
            "_id": {"$in": page_ids},
            "chatbot_id": chatbot_id
        }).to_list(length=None)
        
        # Delete from database
        result = await db.scraped_pages.delete_many({
            "_id": {"$in": page_ids},
            "chatbot_id": chatbot_id
        })
        
        # TODO: Also delete from vector store (ChromaDB)
        # This would require importing the vector store service
    
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")
    
    # Log audit event
    await db.audit_logs.insert_one({
        "action": f"pages:bulk_{request.action}",
        "performed_by": current_user.email,
        "chatbot_id": chatbot_id,
        "details": {
            "page_count": len(request.page_ids),
            "action": request.action,
            "params": request.params
        },
        "timestamp": datetime.utcnow()
    })
    
    # Update chatbot statistics
    await update_chatbot_stats(chatbot_id)
    
    modified_count = result.modified_count if hasattr(result, 'modified_count') else result.deleted_count
    return {"success": True, "modified": modified_count}

@router.get("/{chatbot_id}/pages/statistics")
async def get_page_statistics(
    chatbot_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get detailed statistics about pages"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor", "viewer"]
    )
    
    db = get_database()
    # Aggregation pipeline
    pipeline = [
        {"$match": {"chatbot_id": chatbot_id}},
        {"$group": {
            "_id": None,
            "total_pages": {"$sum": 1},
            "total_size": {"$sum": "$char_count"},
            "pinned_count": {"$sum": {"$cond": [{"$eq": ["$rescrape_policy.is_pinned", True]}, 1, 0]}},
            "active_count": {"$sum": {"$cond": [{"$ne": ["$rescrape_policy.is_pinned", True]}, 1, 0]}},
            "high_priority": {"$sum": {"$cond": [{"$eq": ["$rescrape_policy.priority", 1]}, 1, 0]}},
            "normal_priority": {"$sum": {"$cond": [{"$eq": ["$rescrape_policy.priority", 0]}, 1, 0]}},
            "low_priority": {"$sum": {"$cond": [{"$eq": ["$rescrape_policy.priority", -1]}, 1, 0]}},
            "avg_size": {"$avg": "$char_count"},
            "oldest_scrape": {"$min": "$scraped_at"},
            "newest_scrape": {"$max": "$scraped_at"}
        }}
    ]
    
    result = await db.scraped_pages.aggregate(pipeline).to_list(length=1)
    
    if not result:
        return {
            "total_pages": 0,
            "total_size": 0,
            "pinned_count": 0,
            "active_count": 0,
            "high_priority": 0,
            "normal_priority": 0,
            "low_priority": 0,
            "avg_size": 0
        }
    
    stats = result[0]
    del stats["_id"]
    
    return stats

@router.post("/{chatbot_id}/exclusion-patterns")
async def add_exclusion_pattern(
    chatbot_id: str,
    request: AddExclusionPatternRequest,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Add an exclusion pattern to global chatbot config"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin"]
    )
    
    db = get_database()
    # Add pattern to chatbot config
    result = await db.chatbot_configs.update_one(
        {"_id": _oid(chatbot_id, "chatbot ID")},
        {"$addToSet": {"excluded_url_patterns": request.pattern}}
    )
    
    # Log audit event
    await db.audit_logs.insert_one({
        "action": "chatbot:exclusion_pattern_added",
        "performed_by": current_user.email,
        "chatbot_id": chatbot_id,
        "details": {
            "pattern": request.pattern,
            "reason": request.reason
        },
        "timestamp": datetime.utcnow()
    })
    
    return {"success": True, "pattern": request.pattern}

@router.delete("/{chatbot_id}/exclusion-patterns")
async def remove_exclusion_pattern(
    chatbot_id: str,
    pattern: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Remove an exclusion pattern from chatbot config"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin"]
    )
    
    db = get_database()
    # Remove pattern from chatbot config
    result = await db.chatbot_configs.update_one(
        {"_id": _oid(chatbot_id, "chatbot ID")},
        {"$pull": {"excluded_url_patterns": pattern}}
    )
    
    # Log audit event
    await db.audit_logs.insert_one({
        "action": "chatbot:exclusion_pattern_removed",
        "performed_by": current_user.email,
        "chatbot_id": chatbot_id,
        "details": {"pattern": pattern},
        "timestamp": datetime.utcnow()
    })
    
    return {"success": True}

@router.get("/{chatbot_id}/exclusion-patterns/preview")
async def preview_exclusion_pattern(
    chatbot_id: str,
    pattern: str,
    current_user: User = Depends(get_current_user)
):
    """Preview how many pages would be affected by an exclusion pattern"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor"]
    )
    
    db = get_database()
    # Get all pages for this chatbot
    pages = await db.scraped_pages.find({"chatbot_id": chatbot_id}).to_list(length=None)
    
    affected = []
    for page in pages:
        if matches_pattern(page["url"], [pattern]):
            affected.append({
                "url": page["url"],
                "title": page.get("title", "Untitled")
            })
    
    return {
        "pattern": pattern,
        "affected_count": len(affected),
        "affected_pages": affected[:50],  # Preview first 50
        "total_pages": len(pages)
    }

async def update_chatbot_stats(chatbot_id: str):
    """Update global statistics in chatbot config"""
    db = get_database()
    pipeline = [
        {"$match": {"chatbot_id": chatbot_id}},
        {"$group": {
            "_id": None,
            "total_pages": {"$sum": 1},
            "total_pinned": {"$sum": {"$cond": [{"$eq": ["$rescrape_policy.is_pinned", True]}, 1, 0]}},
            "total_active": {"$sum": {"$cond": [{"$ne": ["$rescrape_policy.is_pinned", True]}, 1, 0]}},
            "total_bytes": {"$sum": "$char_count"}
        }}
    ]
    
    result = await db.scraped_pages.aggregate(pipeline).to_list(length=1)
    
    if result:
        stats = result[0]
        await db.chatbot_configs.update_one(
            {"_id": _oid(chatbot_id, "chatbot ID")},
            {"$set": {
                "total_pages_active": stats.get("total_active", 0),
                "total_pages_pinned": stats.get("total_pinned", 0),
                "total_bytes_stored": stats.get("total_bytes", 0)
            }}
        )


# ══════════════════════════════════════════════════════════════════════════════
# Version History Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{chatbot_id}/pages/{page_id}/versions")
async def get_page_versions(
    chatbot_id: str,
    page_id: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user)
):
    """Get version history for a page"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor", "viewer"]
    )
    
    db = get_database()
    # Verify page belongs to chatbot
    page = await db.scraped_pages.find_one({
        "_id": _oid(page_id, "page ID"),
        "chatbot_id": chatbot_id
    })
    
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    # Get versions
    versions = await db.page_versions.find({
        "page_id": page_id,
        "chatbot_id": chatbot_id
    }).sort("version_number", -1).limit(limit).to_list(length=limit)
    
    # Format response
    formatted_versions = []
    for v in versions:
        formatted_versions.append({
            "id": str(v["_id"]),
            "version_number": v["version_number"],
            "content_hash": v["content_hash"],
            "scraped_at": v["scraped_at"],
            "title": v.get("title"),
            "char_count": v.get("char_count", 0),
            "change_magnitude": v.get("change_magnitude"),
            "change_score": v.get("change_score"),
            "changes_summary": v.get("changes_summary"),
            "quality_score": v.get("quality_score"),
            "quality_grade": v.get("quality_grade"),
            "created_at": v.get("created_at")
        })
    
    return {
        "page_url": page["url"],
        "current_version": page.get("version", 1),
        "total_versions": len(formatted_versions),
        "versions": formatted_versions
    }

@router.post("/{chatbot_id}/pages/{page_id}/restore-version")
async def restore_page_version(
    chatbot_id: str,
    page_id: str,
    version_number: int,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Restore a page to a previous version (placeholder - requires content storage)"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin"]
    )
    
    # This would require storing full content of versions
    # For now, return not implemented
    raise HTTPException(
        status_code=501,
        detail="Version restoration requires full content storage (not yet implemented)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Health Checking Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{chatbot_id}/pages/{page_id}/check-health")
async def check_page_health_endpoint(
    chatbot_id: str,
    page_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Manually trigger health check for a single page"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor"]
    )
    
    db = get_database()
    # Get page
    page = await db.scraped_pages.find_one({
        "_id": _oid(page_id, "page ID"),
        "chatbot_id": chatbot_id
    })
    
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    
    # Check health
    from services.smart_rescrape_service import smart_rescrape_engine
    health_result = await smart_rescrape_engine.check_page_health(page["url"])
    
    # Update page
    update_fields = {
        "is_broken": health_result["is_broken"],
        "http_status": health_result["status"],
        "last_health_check": health_result["checked_at"]
    }
    
    if health_result["is_broken"] and not page.get("is_broken"):
        # Page just became broken
        update_fields["broken_since"] = health_result["checked_at"]
        
        # Send broken link alert
        try:
            from services.alert_service import alert_service
            await alert_service.send_broken_link_alert(
                chatbot_id,
                page,
                health_result["status"],
                health_result.get("error")
            )
        except Exception as e:
            logger.error(f"Failed to send broken link alert: {e}")
    
    elif not health_result["is_broken"] and page.get("is_broken"):
        # Page recovered
        update_fields["broken_since"] = None
    
    await db.scraped_pages.update_one(
        {"_id": _oid(page_id, "page ID")},
        {"$set": update_fields}
    )
    
    # Log action
    await db.audit_logs.insert_one({
        "action": "page_health_check",
        "performed_by": current_user.email,
        "chatbot_id": chatbot_id,
        "details": {
            "page_id": page_id,
            "url": page["url"],
            "status": health_result["status"],
            "is_broken": health_result["is_broken"]
        },
        "timestamp": datetime.utcnow()
    })
    
    return {
        "url": page["url"],
        "health": health_result,
        "updated": True
    }

@router.get("/{chatbot_id}/pages/broken")
async def get_broken_pages(
    chatbot_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get all broken pages for a chatbot"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor", "viewer"]
    )
    
    db = get_database()
    broken_pages = await db.scraped_pages.find({
        "chatbot_id": chatbot_id,
        "is_broken": True
    }).sort("broken_since", -1).to_list(length=None)
    
    return {
        "total_broken": len(broken_pages),
        "pages": [
            {
                "id": str(p["_id"]),
                "url": p["url"],
                "title": p.get("title"),
                "http_status": p.get("http_status"),
                "broken_since": p.get("broken_since"),
                "last_health_check": p.get("last_health_check")
            }
            for p in broken_pages
        ]
    }


# ══════════════════════════════════════════════════════════════════════════════
# Change Alerts Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{chatbot_id}/alerts")
async def get_change_alerts(
    chatbot_id: str,
    acknowledged: Optional[bool] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user)
):
    """Get change alerts for a chatbot"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor", "viewer"]
    )
    
    db = get_database()
    query = {"chatbot_id": chatbot_id}
    if acknowledged is not None:
        query["acknowledged"] = acknowledged
    
    alerts = await db.change_alerts.find(query).sort("created_at", -1).limit(limit).to_list(length=limit)
    
    return {
        "total": len(alerts),
        "alerts": [
            {
                "id": str(a["_id"]),
                "page_id": a["page_id"],
                "url": a["url"],
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

@router.post("/{chatbot_id}/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    chatbot_id: str,
    alert_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """Mark an alert as acknowledged"""
    await check_chatbot_permission(
        chatbot_id,
        current_user.email,
        ["owner", "admin", "editor"]
    )
    
    db = get_database()
    result = await db.change_alerts.update_one(
        {"_id": _oid(alert_id, "alert ID"), "chatbot_id": chatbot_id},
        {"$set": {
            "acknowledged": True,
            "acknowledged_by": current_user.email,
            "acknowledged_at": datetime.utcnow()
        }}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    return {"success": True}
