from fastapi import APIRouter, HTTPException, status, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from models import User, UserCreate, UserLogin, Token, PasswordResetRequest, PasswordResetConfirm, TwoFactorSetupResponse, TwoFactorVerifyRequest, TwoFactorLoginRequest
from core.auth import get_password_hash, verify_password, create_access_token, get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES
from core.database import get_database
from datetime import timedelta, datetime
from slowapi import Limiter
from slowapi.util import get_remote_address
from core.logging_config import security_logger
from core.csrf_protection import generate_csrf_token, set_csrf_cookie, validate_csrf_token
from services.email_service import send_password_reset_email, send_two_factor_enabled_email
import re
import os
import html
import secrets
import pyotp
import qrcode
import io
import base64

router = APIRouter(prefix="/auth", tags=["Authentication"])
limiter = Limiter(key_func=get_remote_address)

def validate_password_strength(password: str):
    """Validate password meets security requirements"""
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long"
        )
    if not re.search(r"[A-Z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one uppercase letter"
        )
    if not re.search(r"[a-z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one lowercase letter"
        )
    if not re.search(r"[0-9]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one number"
        )
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>_\-+=\[\]\\\/~`]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one special character (!@#$%^&*...)"
        )

def _set_auth_cookie(response: Response, access_token: str):
    """Centralized cookie setting to avoid inconsistencies"""
    cookie_secure = os.getenv("COOKIE_SECURE", "True").lower() == "true"
    samesite = os.getenv("COOKIE_SAMESITE", "lax")
    response.set_cookie(
        key="access_token_v2",
        value=access_token,
        httponly=True,
        secure=cookie_secure,
        samesite=samesite,
        max_age=60 * 60 * 24,  # 1 day
        path="/",
    )

def _sanitize_input(value: str, max_length: int = 255) -> str:
    """Sanitize user input to prevent injection attacks"""
    if not value:
        return value
    return html.escape(value.strip()[:max_length])

def _generate_backup_codes(count: int = 8) -> list:
    """Generate backup codes for 2FA recovery"""
    return [secrets.token_hex(4).upper() for _ in range(count)]

def _generate_qr_code(data: str) -> str:
    """Generate QR code as base64 encoded image"""
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

@router.post("/login", response_model=Token)
@limiter.limit("5/minute")  # Rate limit: 5 login attempts per minute
async def login(response: Response, request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    db = get_database()
    
    # Find user by email (username field in OAuth2 form)
    # Use case-insensitive lookup to tolerate different capitalizations
    login_email = _sanitize_input(form_data.username).lower() if form_data.username else form_data.username
    # Indexed, case-insensitive exact match via the users_email_unique_ci
    # collation — avoids a per-login regex scan and its enumeration/ReDoS surface.
    user = await db.users.find_one(
        {"email": login_email}, collation={"locale": "en", "strength": 2}
    )
    
    client_ip = request.client.host if request.client else "unknown"
    
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        # Log failed login attempt
        security_logger.log_failed_login(form_data.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if 2FA is enabled
    if user.get("two_factor_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Two-factor authentication required. Please use the 2FA login endpoint."
        )
    
    # Log successful login
    security_logger.log_successful_login(user["email"], client_ip)
    
    # Create access token (use stored email as canonical)
    access_token = create_access_token(data={"sub": user["email"]})
    
    # Set access token in httpOnly cookie
    _set_auth_cookie(response, access_token)
    
    # Return token in body as well for client-side use if needed, 
    # but client should prefer cookie for subsequent requests
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/login-json", response_model=Token)
@limiter.limit("5/minute")  # Rate limit: 5 login attempts per minute
async def login_json(response: Response, request: Request, user_login: UserLogin):
    db = get_database()
    
    # Case-insensitive lookup
    lookup_email = _sanitize_input(user_login.email).lower() if user_login.email else user_login.email
    user = await db.users.find_one(
        {"email": lookup_email}, collation={"locale": "en", "strength": 2}
    )
    
    client_ip = request.client.host if request.client else "unknown"
    
    if not user or not verify_password(user_login.password, user["hashed_password"]):
        # Log failed login attempt
        security_logger.log_failed_login(user_login.email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    
    # Check if 2FA is enabled
    if user.get("two_factor_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Two-factor authentication required. Please use the 2FA login endpoint."
        )
    
    # Log successful login
    security_logger.log_successful_login(user["email"], client_ip)
    
    access_token = create_access_token(data={"sub": user["email"]})
    
    # Set access token in httpOnly cookie
    _set_auth_cookie(response, access_token)
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/logout")
async def logout(response: Response):
    """Logout by clearing auth cookies."""
    response.delete_cookie(
        key="access_token_v2",
        path="/",
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key="csrf_token",
        path="/",
    )
    return {"message": "Logged out successfully"}

@router.get("/csrf-token")
async def get_csrf_token(response: Response):
    """
    Generate and return CSRF token for admin application.
    Token is set in cookie and returned in response body.
    Uses double-submit cookie pattern for CSRF protection.
    """
    token = generate_csrf_token()
    set_csrf_cookie(response, token)
    
    return {"csrf_token": token}

@router.get("/me")
async def read_users_me(current_user: User = Depends(get_current_user)):
    """
    Get current user profile.
    Used for verifying session on frontend load.

    Returns only non-sensitive fields — never the password hash, 2FA secret,
    backup codes or reset tokens (the full User model contains those).
    """
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "username": current_user.username,
        "is_superuser": current_user.is_superuser,
        "can_create_chatbot": current_user.can_create_chatbot,
        "two_factor_enabled": current_user.two_factor_enabled,
        "created_at": current_user.created_at,
    }


@router.put("/change-password")
async def change_password(
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),
):
    """Change current user's password."""
    body = await request.json()
    current_password = body.get("current_password", "")
    new_password = body.get("new_password", "")

    if not verify_password(current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    validate_password_strength(new_password)

    db = get_database()
    await db.users.update_one(
        {"email": current_user.email},
        {"$set": {"hashed_password": get_password_hash(new_password)}},
    )

    client_ip = request.client.host if request.client else "unknown"
    security_logger.log_successful_login(current_user.email, client_ip)  # reuse log
    return {"message": "Password changed successfully"}


@router.put("/profile")
async def update_profile(
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),
):
    """Update current user's profile (username)."""
    body = await request.json()
    new_username = _sanitize_input(body.get("username", ""))

    if not new_username or len(new_username) < 3 or len(new_username) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be between 3 and 50 characters",
        )
    if not re.match(r"^[a-zA-Z0-9_-]+$", new_username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username can only contain letters, numbers, underscores and hyphens",
        )

    db = get_database()
    existing = await db.users.find_one({"username": new_username})
    if existing and existing["email"] != current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken",
        )

    await db.users.update_one(
        {"email": current_user.email},
        {"$set": {"username": new_username}},
    )
    return {"message": "Profile updated successfully", "username": new_username}


@router.post("/forgot-password")
@limiter.limit("3/hour")  # Rate limit: 3 requests per hour
async def forgot_password(request: Request, password_reset: PasswordResetRequest):
    """Request password reset email."""
    db = get_database()
    
    email = _sanitize_input(password_reset.email).lower()
    user = await db.users.find_one({"email": email})
    
    # Don't reveal if email exists for security
    if not user:
        return {"message": "If the email exists, a password reset link has been sent."}
    
    # Generate reset token
    reset_token = secrets.token_urlsafe(32)
    reset_expires = datetime.utcnow() + timedelta(hours=1)
    
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "reset_password_token": reset_token,
                "reset_password_token_expires": reset_expires
            }
        }
    )
    
    # Send reset email
    try:
        send_password_reset_email(email, reset_token)
    except Exception as e:
        security_logger.logger.error(f"Failed to send password reset email: {str(e)}")
    
    return {"message": "If the email exists, a password reset link has been sent."}


@router.post("/reset-password")
async def reset_password(request: PasswordResetConfirm):
    """Reset password with token."""
    db = get_database()
    
    user = await db.users.find_one({
        "reset_password_token": request.token,
        "reset_password_token_expires": {"$gt": datetime.utcnow()}
    })
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token"
        )
    
    # Validate new password
    validate_password_strength(request.new_password)
    
    # Update password and remove reset token
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "hashed_password": get_password_hash(request.new_password),
                "reset_password_token": None,
                "reset_password_token_expires": None
            }
        }
    )
    
    return {"message": "Password reset successfully. You can now log in with your new password."}

@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_two_factor(
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """Generate 2FA secret and QR code for user to scan with authenticator app."""
    db = get_database()
    
    # Check if 2FA is already enabled
    if current_user.two_factor_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is already enabled"
        )
    
    # Generate secret
    secret = pyotp.random_base32()
    
    # Generate backup codes
    backup_codes = _generate_backup_codes()
    
    # Hash backup codes before storing
    hashed_backup_codes = [get_password_hash(code) for code in backup_codes]
    
    # Store secret and backup codes temporarily (not enabled yet)
    await db.users.update_one(
        {"email": current_user.email},
        {
            "$set": {
                "two_factor_secret": secret,
                "backup_codes": hashed_backup_codes
            }
        }
    )
    
    # Generate QR code
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.email,
        issuer_name="Chatbot Platform"
    )
    qr_code = _generate_qr_code(totp_uri)
    
    return {
        "secret": secret,
        "qr_code": qr_code,
        "backup_codes": backup_codes  # Return plain codes for user to save
    }


@router.post("/2fa/verify")
async def verify_two_factor(
    request: TwoFactorVerifyRequest,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """Verify 2FA code and enable 2FA for the user."""
    db = get_database()
    
    # Get user's 2FA secret
    user = await db.users.find_one({"email": current_user.email})
    if not user or not user.get("two_factor_secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication setup not found. Please start setup first."
        )
    
    secret = user["two_factor_secret"]
    totp = pyotp.TOTP(secret)
    
    # Verify the code
    if not totp.verify(request.code, valid_window=1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code"
        )
    
    # Enable 2FA
    await db.users.update_one(
        {"email": current_user.email},
        {"$set": {"two_factor_enabled": True}}
    )
    
    # Send confirmation email
    try:
        send_two_factor_enabled_email(current_user.email)
    except Exception as e:
        security_logger.logger.error(f"Failed to send 2FA enabled email: {str(e)}")
    
    return {"message": "Two-factor authentication enabled successfully"}


@router.post("/2fa/disable")
async def disable_two_factor(
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """Disable 2FA for the user."""
    db = get_database()
    
    body = await request.json()
    password = body.get("password", "")
    
    # Verify password before disabling 2FA
    if not verify_password(password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect password"
        )
    
    # Disable 2FA and remove secrets
    await db.users.update_one(
        {"email": current_user.email},
        {
            "$set": {
                "two_factor_enabled": False,
                "two_factor_secret": None,
                "backup_codes": []
            }
        }
    )
    
    return {"message": "Two-factor authentication disabled successfully"}


@router.post("/2fa/login", response_model=Token)
@limiter.limit("5/minute")
async def login_with_2fa(
    response: Response,
    request: Request,
    login_request: TwoFactorLoginRequest
):
    """Login with 2FA code."""
    db = get_database()
    
    # Find user
    email = _sanitize_input(login_request.email).lower()
    user = await db.users.find_one({"email": email})
    
    client_ip = request.client.host if request.client else "unknown"
    
    if not user or not verify_password(login_request.password, user["hashed_password"]):
        security_logger.log_failed_login(email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    
    # Check if 2FA is enabled
    if not user.get("two_factor_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is not enabled for this account"
        )
    
    # Verify 2FA code
    secret = user.get("two_factor_secret")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="2FA configuration error"
        )
    
    totp = pyotp.TOTP(secret)
    code_valid = totp.verify(login_request.two_factor_code, valid_window=1)
    
    # If code is not valid, check backup codes
    if not code_valid:
        backup_codes = user.get("backup_codes", [])
        backup_code_valid = False
        
        for i, hashed_code in enumerate(backup_codes):
            if verify_password(login_request.two_factor_code, hashed_code):
                backup_code_valid = True
                # Remove used backup code
                backup_codes.pop(i)
                await db.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"backup_codes": backup_codes}}
                )
                break
        
        if not backup_code_valid:
            security_logger.log_failed_login(email, client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid 2FA code"
            )
    
    # Log successful login
    security_logger.log_successful_login(email, client_ip)
    
    # Create access token
    access_token = create_access_token(data={"sub": user["email"]})
    
    # Set access token in httpOnly cookie
    _set_auth_cookie(response, access_token)
    
    return {"access_token": access_token, "token_type": "bearer"}


# ── Superuser: User Management ─────────────────────────────────────────────────

def _require_superuser(current_user: User):
    """Raise 403 if current_user is not a superuser."""
    if not getattr(current_user, 'is_superuser', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser access required"
        )


@router.get("/users", response_model=list)
async def list_users(current_user: User = Depends(get_current_user)):
    """List all users. Superuser only."""
    _require_superuser(current_user)
    db = get_database()
    users = []
    async for u in db.users.find({}, {
        "_id": 1, "email": 1, "username": 1, "created_at": 1,
        "is_superuser": 1, "can_create_chatbot": 1, "created_by": 1,
    }):
        u["_id"] = str(u["_id"])
        users.append(u)
    return users


@router.post("/users", response_model=dict)
async def create_managed_user(
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),
):
    """Create a managed user. Superuser only. Cannot create chatbots."""
    _require_superuser(current_user)
    db = get_database()
    body = await request.json()

    email = _sanitize_input(body.get("email", "")).lower()
    username = _sanitize_input(body.get("username", ""))
    password = body.get("password", "")

    if not email or not username or not password:
        raise HTTPException(status_code=400, detail="email, username, and password are required")

    validate_password_strength(password)

    # Check uniqueness (indexed, case-insensitive via collation)
    if await db.users.find_one({"email": email}, collation={"locale": "en", "strength": 2}):
        raise HTTPException(status_code=400, detail="Email already registered")
    if await db.users.find_one({"username": username}):
        raise HTTPException(status_code=400, detail="Username already taken")

    user_dict = {
        "email": email,
        "username": username,
        "hashed_password": get_password_hash(password),
        "is_superuser": False,
        "can_create_chatbot": False,   # managed users cannot create chatbots
        "created_by": current_user.email,
        "two_factor_enabled": False,
        "backup_codes": [],
    }
    result = await db.users.insert_one(user_dict)
    return {"message": "User created successfully", "user_id": str(result.inserted_id), "email": email}


@router.put("/users/{user_id}", response_model=dict)
async def update_managed_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),
):
    """Update a managed user's username or password. Superuser only."""
    _require_superuser(current_user)
    from bson import ObjectId
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID")

    db = get_database()
    body = await request.json()
    update_data = {}

    if "username" in body:
        new_username = _sanitize_input(body["username"])
        if not new_username or len(new_username) < 3:
            raise HTTPException(status_code=400, detail="Username too short")
        existing = await db.users.find_one({"username": new_username})
        if existing and str(existing["_id"]) != user_id:
            raise HTTPException(status_code=400, detail="Username already taken")
        update_data["username"] = new_username

    if "password" in body:
        validate_password_strength(body["password"])
        update_data["hashed_password"] = get_password_hash(body["password"])

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User updated successfully"}


@router.delete("/users/{user_id}", response_model=dict)
async def delete_managed_user(
    user_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),
):
    """Delete a managed user. Superuser only. Cannot delete self."""
    _require_superuser(current_user)
    from bson import ObjectId
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID")

    if str(current_user.id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    db = get_database()
    result = await db.users.delete_one({"_id": ObjectId(user_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted successfully"}