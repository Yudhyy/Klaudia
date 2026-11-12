"""Retain supported imports from the installed service."""

from klaudia_api.adapters.identity import PostgresIdentityStore as PostgresIdentityStore
from klaudia_api.adapters.identity import UserCredentials as UserCredentials
from klaudia_api.adapters.identity import asyncpg as asyncpg
