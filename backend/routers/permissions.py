from fastapi import APIRouter, HTTPException, Depends, status, Request
from typing import List
from models import ChatbotPermission, ChatbotPermissionCreate, ChatbotPermissionUpdate, User, PermissionLevel
from core.auth import get_current_user
from core.database import get_database
from core.csrf_protection import validate_csrf_token
from services.audit_service import log_action
from bson import ObjectId
from datetime import datetime

router = APIRouter(prefix="/permissions", tags=["Chatbot Permissions"])

async def check_permission(chatbot_id: str, user_email: str, required_level: str = None) -> dict:
    """
    Check if a user has permission for a chatbot.
    Returns permission info or raises HTTPException.
    """
    db = get_database()
    
    if not ObjectId.is_valid(chatbot_id):
        raise HTTPException(status_code=400, detail="Invalid chatbot ID")
    
    # Check if user is the owner
    chatbot = await db.chatbot_configs.find_one({"_id": ObjectId(chatbot_id)})
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    
    # Get user info
    user = await db.users.find_one({"email": user_email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    is_owner = chatbot.get("user_id") == str(user["_id"])
    
    # Owner has all permissions
    if is_owner:
        return {
            "is_owner": True,
            "permission_level": "owner",
            "chatbot": chatbot,
            "user": user
        }
    
    # Check permission in database
    permission = await db.chatbot_permissions.find_one({
        "chatbot_id": chatbot_id,
        "user_email": user_email
    })
    
    if not permission:
        raise HTTPException(status_code=403, detail="You don't have permission to access this chatbot")
    
    # Check if required level is met
    if required_level:
        level_hierarchy = {
            PermissionLevel.VIEWER: 1,
            PermissionLevel.EDITOR: 2,
            PermissionLevel.ADMIN: 3,
            "owner": 4
        }
        
        current_level = level_hierarchy.get(permission["permission_level"], 0)
        required = level_hierarchy.get(required_level, 0)
        
        if current_level < required:
            raise HTTPException(
                status_code=403, 
                detail=f"You need {required_level} permission for this action"
            )
    
    return {
        "is_owner": False,
        "permission_level": permission["permission_level"],
        "chatbot": chatbot,
        "user": user,
        "permission": permission
    }


async def get_user_chatbots(user_email: str) -> List[str]:
    """
    Get all chatbot IDs that a user has access to (owned or shared).
    """
    db = get_database()
    
    # Get user
    user = await db.users.find_one({"email": user_email})
    if not user:
        return []
    
    chatbot_ids = []
    
    # Get owned chatbots
    async for chatbot in db.chatbot_configs.find({"user_id": str(user["_id"])}):
        chatbot_ids.append(str(chatbot["_id"]))
    
    # Get shared chatbots
    async for permission in db.chatbot_permissions.find({"user_email": user_email}):
        chatbot_ids.append(permission["chatbot_id"])
    
    return list(set(chatbot_ids))  # Remove duplicates


@router.post("/", response_model=ChatbotPermission)
async def grant_permission(
    permission_data: ChatbotPermissionCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """
    Grant permission to a user for a chatbot.
    Only the owner or admin can grant permissions.
    """
    db = get_database()
    
    # Check if current user has permission to grant access (must be owner or admin)
    perm_check = await check_permission(
        permission_data.chatbot_id,
        current_user.email,
        required_level=PermissionLevel.ADMIN
    )
    
    # Check if permission already exists
    existing = await db.chatbot_permissions.find_one({
        "chatbot_id": permission_data.chatbot_id,
        "user_email": permission_data.user_email
    })
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Permission already exists for this user. Use PUT to update."
        )
    
    # Check if target user exists
    target_user = await db.users.find_one({"email": permission_data.user_email})
    if not target_user:
        raise HTTPException(
            status_code=404,
            detail=f"User with email {permission_data.user_email} not found. They need to create an account first."
        )
    
    # Don't allow granting permission to the owner
    if perm_check["is_owner"] and permission_data.user_email == current_user.email:
        raise HTTPException(
            status_code=400,
            detail="Cannot grant permission to the chatbot owner"
        )
    
    permission_dict = {
        "chatbot_id": permission_data.chatbot_id,
        "user_email": permission_data.user_email,
        "permission_level": permission_data.permission_level,
        "granted_by": current_user.email,
        "granted_at": datetime.utcnow()
    }
    
    result = await db.chatbot_permissions.insert_one(permission_dict)
    permission_dict["_id"] = str(result.inserted_id)

    # Audit log
    await log_action(
        action="permission:granted",
        performed_by=current_user.email,
        chatbot_id=permission_data.chatbot_id,
        target_user=permission_data.user_email,
        details={"permission_level": permission_data.permission_level},
        ip_address=request.client.host if request.client else None,
    )

    return permission_dict


@router.get("/chatbot/{chatbot_id}", response_model=List[ChatbotPermission])
async def get_chatbot_permissions(
    chatbot_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Get all permissions for a specific chatbot.
    Only owner or admin can view permissions.
    """
    # Check permission (owner or admin)
    await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.ADMIN)
    
    db = get_database()
    
    permissions = []
    async for perm in db.chatbot_permissions.find({"chatbot_id": chatbot_id}):
        perm["_id"] = str(perm["_id"])
        permissions.append(perm)
    
    return permissions


@router.put("/{permission_id}", response_model=ChatbotPermission)
async def update_permission(
    permission_id: str,
    permission_update: ChatbotPermissionUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """
    Update a permission level.
    Only owner or admin can update permissions.
    """
    db = get_database()
    
    if not ObjectId.is_valid(permission_id):
        raise HTTPException(status_code=400, detail="Invalid permission ID")
    
    # Get the permission
    permission = await db.chatbot_permissions.find_one({"_id": ObjectId(permission_id)})
    if not permission:
        raise HTTPException(status_code=404, detail="Permission not found")
    
    # Check if current user can modify this permission
    await check_permission(
        permission["chatbot_id"],
        current_user.email,
        required_level=PermissionLevel.ADMIN
    )
    
    # Update permission
    await db.chatbot_permissions.update_one(
        {"_id": ObjectId(permission_id)},
        {"$set": {"permission_level": permission_update.permission_level}}
    )
    
    # Return updated permission
    updated_permission = await db.chatbot_permissions.find_one({"_id": ObjectId(permission_id)})
    updated_permission["_id"] = str(updated_permission["_id"])

    # Audit log
    await log_action(
        action="permission:updated",
        performed_by=current_user.email,
        chatbot_id=permission["chatbot_id"],
        target_user=permission["user_email"],
        details={
            "old_level": permission["permission_level"],
            "new_level": permission_update.permission_level,
        },
        ip_address=request.client.host if request.client else None,
    )

    return updated_permission


@router.delete("/{permission_id}")
async def revoke_permission(
    permission_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """
    Revoke a permission.
    Only owner or admin can revoke permissions.
    """
    db = get_database()
    
    if not ObjectId.is_valid(permission_id):
        raise HTTPException(status_code=400, detail="Invalid permission ID")
    
    # Get the permission
    permission = await db.chatbot_permissions.find_one({"_id": ObjectId(permission_id)})
    if not permission:
        raise HTTPException(status_code=404, detail="Permission not found")
    
    # Check if current user can revoke this permission
    await check_permission(
        permission["chatbot_id"],
        current_user.email,
        required_level=PermissionLevel.ADMIN
    )
    
    # Delete permission
    result = await db.chatbot_permissions.delete_one({"_id": ObjectId(permission_id)})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Permission not found")

    # Audit log
    await log_action(
        action="permission:revoked",
        performed_by=current_user.email,
        chatbot_id=permission["chatbot_id"],
        target_user=permission["user_email"],
        details={"permission_level": permission["permission_level"]},
        ip_address=request.client.host if request.client else None,
    )

    return {"message": "Permission revoked successfully"}


@router.get("/my-chatbots")
async def get_my_accessible_chatbots(current_user: User = Depends(get_current_user)):
    """
    Get all chatbots that the current user can access (owned or shared).
    Returns list with permission info.
    """
    db = get_database()
    
    chatbots = []
    
    # Get owned chatbots
    async for chatbot in db.chatbot_configs.find({"user_id": str(current_user.id)}):
        chatbot["_id"] = str(chatbot["_id"])
        chatbot["permission_level"] = "owner"
        chatbot["is_owner"] = True
        chatbots.append(chatbot)
    
    # Get shared chatbots
    async for permission in db.chatbot_permissions.find({"user_email": current_user.email}):
        chatbot = await db.chatbot_configs.find_one({"_id": ObjectId(permission["chatbot_id"])})
        if chatbot:
            chatbot["_id"] = str(chatbot["_id"])
            chatbot["permission_level"] = permission["permission_level"]
            chatbot["is_owner"] = False
            chatbot["granted_by"] = permission.get("granted_by")
            chatbot["granted_at"] = permission.get("granted_at")
            chatbots.append(chatbot)
    
    return chatbots
