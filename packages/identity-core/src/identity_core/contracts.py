"""Validated identity requests, access grants and use-case errors."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Registration:
    """Account fields validated by the caller before registration."""

    username: str
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class Login:
    """Credentials supplied for an exact account lookup."""

    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class AuthenticatedAccount:
    """Account identity and its issued access token."""

    user_id: int
    username: str
    access_token: str = field(repr=False)


class UsernameTakenError(Exception):
    """The exact username already belongs to an account."""


class AccountCreationError(Exception):
    """Password hashing or account creation failed."""


class InvalidCredentialsError(Exception):
    """The account is unknown or its password does not match."""
