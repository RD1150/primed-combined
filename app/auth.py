from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from app.config import get_settings
from app.database import get_db
from app.models import User

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str]
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": user_id, "exp": expire}, settings.secret_key, algorithm=settings.algorithm)

def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload.get("sub")
    except JWTError:
        return None

def create_password_reset_token(user_id: str, password_set_at: Optional[datetime]) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_token_expire_minutes)
    psa = password_set_at.isoformat() if password_set_at else ""
    return jwt.encode(
        {"sub": user_id, "exp": expire, "purpose": "password_reset", "psa": psa},
        settings.secret_key,
        algorithm=settings.algorithm,
    )

def decode_password_reset_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    if payload.get("purpose") != "password_reset":
        return None
    return payload

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def _send_setup_link(user: User) -> None:
    from app.email_service import send_password_reset_email
    token = create_password_reset_token(user.id, user.password_set_at)
    reset_url = f"{settings.app_base_url.rstrip('/')}/app#/reset-password?token={token}"
    send_password_reset_email(user.email, reset_url)

async def register_user(data: UserCreate, db: AsyncSession):
    result = await db.execute(select(User).where(User.email == data.email))
    existing = result.scalar_one_or_none()
    if existing:
        if existing.hashed_password is None:
            _send_setup_link(existing)
            return {"password_setup_required": True, "email": existing.email}
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=data.email, hashed_password=hash_password(data.password), name=data.name, password_set_at=datetime.now(timezone.utc))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at))

async def login_user(data: UserLogin, db: AsyncSession):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.hashed_password is None:
        _send_setup_link(user)
        return {"password_setup_required": True, "email": user.email}
    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at))

async def request_password_reset(data: ForgotPasswordRequest, db: AsyncSession) -> None:
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user:
        return
    _send_setup_link(user)

async def reset_password(data: ResetPasswordRequest, db: AsyncSession) -> TokenResponse:
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    payload = decode_password_reset_token(data.token)
    if not payload:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    expected_psa = user.password_set_at.isoformat() if user.password_set_at else ""
    if payload.get("psa", "") != expected_psa:
        raise HTTPException(status_code=400, detail="This reset link has already been used")
    user.hashed_password = hash_password(data.new_password)
    user.password_set_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    access_token = create_access_token(user.id)
    return TokenResponse(access_token=access_token, user=UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at))
