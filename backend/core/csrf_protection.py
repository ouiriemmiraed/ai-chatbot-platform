import secrets
import hmac
from fastapi import Request, HTTPException, status
from fastapi.responses import Response
import os

CSRF_TOKEN_LENGTH = 32
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

def generate_csrf_token() -> str:
    """Generate a secure CSRF token"""
    return secrets.token_urlsafe(CSRF_TOKEN_LENGTH)

def set_csrf_cookie(response: Response, token: str):
    """Set CSRF token in cookie"""
    cookie_secure = os.getenv("COOKIE_SECURE", "True").lower() == "true"
    
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # JS needs to read it for double-submit pattern
        secure=cookie_secure,  # Configurable for dev/prod
        samesite="lax",  # Relaxed for dev, strict for prod
        max_age=60 * 60 * 24  # 24 hours
    )

def validate_csrf_token(request: Request):
    """
    Validate CSRF token using double-submit cookie pattern.
    Token must be present in both cookie and custom header, and they must match.
    """
    # Get token from cookie
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    
    # Get token from header
    header_token = request.headers.get(CSRF_HEADER_NAME)
    
    # Both must exist
    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing"
        )
    
    # They must match (use constant-time comparison to prevent timing attacks)
    if not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token validation failed"
        )
    
    return True
