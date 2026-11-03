"""Auth routes: register and login, both returning a bearer access token."""

import datetime
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.helpers.ratelimit import auth_limit, limiter
from app.services.auth.passwords import hash_password, verify_password
from app.services.auth.store import IdentityStore
from app.services.auth.tokens import create_access_token
from config.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


def _issue_token(user_id: int) -> str:
    settings = get_settings()
    return create_access_token(
        user_id=user_id,
        secret=settings.jwt_secret,
        expires_delta=datetime.timedelta(days=settings.jwt_expires_days),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
@limiter.limit(auth_limit)
async def register(
    body: RegisterRequest, request: Request, response: Response
) -> TokenResponse:
    """Create a user account and return an access token."""
    identity: IdentityStore = request.app.state.container.identity_store
    if await identity.get_user_by_username(body.username) is not None:
        raise HTTPException(status_code=409, detail="Username already taken")
    try:
        user_id = await identity.create_user(
            username=body.username,
            email=body.email,
            password_hash=hash_password(body.password),
        )
    except Exception:
        # UNIQUE(email) violation lands here; don't leak which field clashed
        # beyond what registration UX needs.
        logger.info("register rejected for %s (constraint)", body.username)
        raise HTTPException(status_code=409, detail="Account already exists") from None
    return TokenResponse(
        access_token=_issue_token(user_id),
        user_id=user_id,
        username=body.username,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit(auth_limit)
async def login(
    body: LoginRequest, request: Request, response: Response
) -> TokenResponse:
    """Verify credentials and return an access token."""
    identity: IdentityStore = request.app.state.container.identity_store
    user = await identity.get_user_by_username(body.username)
    if user is None or not verify_password(body.password, user["password_hash"]):
        # Same error for unknown user and wrong password (no enumeration).
        raise HTTPException(status_code=401, detail="Invalid username or password")
    await identity.update_last_login(user["user_id"])
    return TokenResponse(
        access_token=_issue_token(user["user_id"]),
        user_id=user["user_id"],
        username=user["username"],
    )
