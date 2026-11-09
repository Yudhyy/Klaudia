"""Auth routes: register and login, both returning a bearer access token."""

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.helpers.ratelimit import auth_limit, limiter
from identity_core.contracts import (
    AccountCreationError,
    InvalidCredentialsError,
    Login,
    Registration,
    UsernameTakenError,
)
from identity_core.service import IdentityService

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


@router.post("/register", response_model=TokenResponse, status_code=201)
@limiter.limit(auth_limit)
async def register(
    body: RegisterRequest, request: Request, response: Response
) -> TokenResponse:
    """Create a user account and return an access token."""
    identity: IdentityService = request.app.state.container.identity_service
    try:
        account = await identity.register(
            Registration(body.username, body.email, body.password)
        )
    except UsernameTakenError:
        raise HTTPException(status_code=409, detail="Username already taken") from None
    except AccountCreationError:
        logger.info("register rejected for %s (constraint)", body.username)
        raise HTTPException(status_code=409, detail="Account already exists") from None
    return TokenResponse(
        access_token=account.access_token,
        user_id=account.user_id,
        username=account.username,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit(auth_limit)
async def login(
    body: LoginRequest, request: Request, response: Response
) -> TokenResponse:
    """Verify credentials and return an access token."""
    identity: IdentityService = request.app.state.container.identity_service
    try:
        account = await identity.login(Login(body.username, body.password))
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=401, detail="Invalid username or password"
        ) from None
    return TokenResponse(
        access_token=account.access_token,
        user_id=account.user_id,
        username=account.username,
    )
