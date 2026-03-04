import os
import hashlib
import bcrypt
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from database import get_user_by_id, get_user_by_username

# ── Config ────────────────────────────────────────────────────

SECRET_KEY = os.getenv("AUTH_SECRET", "dev-secret-change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

# ── Password hashing ─────────────────────────────────────────

def _prep_password(password: str) -> bytes:
    """Pre-hash password with SHA-256 to handle bcrypt's 72-byte limit.
    
    Returns 32 bytes (SHA-256 digest) which is well under bcrypt's 72-byte limit.
    """
    return hashlib.sha256(password.encode('utf-8')).digest()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt with SHA-256 pre-hashing."""
    # Pre-hash to handle passwords longer than 72 bytes
    prepped = _prep_password(password)
    # Generate salt and hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(prepped, salt)
    # Return as string (bcrypt returns bytes)
    return hashed.decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    # Pre-hash to match what hash_password does
    prepped = _prep_password(plain)
    # bcrypt expects bytes
    hashed_bytes = hashed.encode('utf-8')
    return bcrypt.checkpw(prepped, hashed_bytes)


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
