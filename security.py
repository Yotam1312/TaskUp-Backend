from jose import jwt, JWTError
from cryptography.fernet import Fernet
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import secrets

load_dotenv()

# ── JWT (Access Token) ─────────────────────────────────────────────────────────
JWT_SECRET: str = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET is not set in .env")
assert isinstance(JWT_SECRET, str)

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # שעה אחת


def create_access_token(user_id: int) -> str:
    """יוצר access token קצר טווח."""
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """
    מפענח ומאמת access token.
    מחזיר user_id אם תקין, None אם לא.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return int(payload["sub"])
    except JWTError:
        return None


# ── Refresh Token ──────────────────────────────────────────────────────────────
REFRESH_TOKEN_EXPIRE_DAYS = 1460  # 4 שנים


def generate_refresh_token() -> str:
    """מייצר refresh token אקראי ומאובטח."""
    return secrets.token_urlsafe(64)


def get_refresh_token_expiry() -> datetime:
    return datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)


# ── Fernet (הצפנת wstoken) ────────────────────────────────────────────────────
FERNET_KEY: str = os.getenv("FERNET_KEY", "")
if not FERNET_KEY:
    raise ValueError("FERNET_KEY is not set in .env")

fernet = Fernet(FERNET_KEY.encode())


def encrypt_token(token: str) -> str:
    """מצפין את ה-wstoken לפני שמירה בDB."""
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """מפענח את ה-wstoken בשליפה מהDB."""
    return fernet.decrypt(encrypted.encode()).decode()