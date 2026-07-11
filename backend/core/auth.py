from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from models import TokenData, User
from core.database import get_database
from core.logging_config import logger
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")

# Enforce SECRET_KEY requirement - fail fast if not set or too weak
if not SECRET_KEY:
    raise ValueError(
        "SECRET_KEY environment variable must be set! "
        "Generate a strong key with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
    )

if len(SECRET_KEY) < 32:
    raise ValueError(
        f"SECRET_KEY must be at least 32 characters long (current: {len(SECRET_KEY)}). "
        "Generate a strong key with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day (reduced from 5 days)
REFRESH_TOKEN_EXPIRE_DAYS = 7  # 7 days (reduced from 14)

# Use argon2 instead of bcrypt to avoid compatibility issues
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
# Allow missing header so we can check cookie
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Check for token in cookie if not in header
    if not token:
        cookie_token = request.cookies.get("access_token_v2") or request.cookies.get("access_token")
        # print(f"DEBUG: Cookies received: {request.cookies.keys()}")
        # print(f"DEBUG: Access token cookie: {cookie_token}")
        if cookie_token:
            if cookie_token.startswith("Bearer "):
                token = cookie_token.split(" ")[1]
            else:
                token = cookie_token
    
    if not token:
        raise credentials_exception
        
    try:
        # print(f"DEBUG: Validating token with key starting with: {SECRET_KEY[:4]}...")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # Reject refresh tokens used as access tokens. Both token types share the
        # same signing key, so without this check a 7-day refresh token would be
        # accepted as a credential, defeating the short access-token window.
        if payload.get("type") != "access":
            raise credentials_exception
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    
    db = get_database()
    user = await db.users.find_one({"email": token_data.email})
    if user is None:
        raise credentials_exception
    return User(**user)
