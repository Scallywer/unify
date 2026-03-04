import os
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import get_user_by_id, get_user_by_username

# ── Config ────────────────────────────────────────────────────

SECRET_KEY = os.getenv("AUTH_SECRET", "dev-secret-change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

# ── Password hashing ─────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _prep_password(password: str) -> str:
    """Pre-hash password with SHA-256 to handle bcrypt's 72-byte limit.
    
    Returns a hex string (64 characters) which is well under bcrypt's 72-byte limit.
    """
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def hash_password(password: str) -> str:
    # Pre-hash to handle passwords longer than 72 bytes
    prepped = _prep_password(password)
    return pwd_context.hash(prepped)


def verify_password(plain: str, hashed: str) -> bool:
    # Pre-hash to match what hash_password does
    prepped = _prep_password(plain)
    return pwd_context.verify(prepped, hashed)


# ── JWT helpers ───────────────────────────────────────────────

def create_access_token(user_id: int) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> int:
    """Return the user_id from the token or raise."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
        return user_id
    except (JWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


# ── FastAPI dependency ────────────────────────────────────────

_bearer_scheme = HTTPBearer()


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict:
    """FastAPI dependency — returns the full user dict or raises 401."""
    user_id = decode_access_token(creds.credentials)
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
