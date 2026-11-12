"""Retain supported imports from the installed service."""

from klaudia_api.interfaces.http.v1 import APIRouter as APIRouter
from klaudia_api.interfaces.http.v1 import approvals_router as approvals_router
from klaudia_api.interfaces.http.v1 import auth_router as auth_router
from klaudia_api.interfaces.http.v1 import chat_router as chat_router
from klaudia_api.interfaces.http.v1 import health_router as health_router
from klaudia_api.interfaces.http.v1 import memory_router as memory_router
from klaudia_api.interfaces.http.v1 import resources_router as resources_router
from klaudia_api.interfaces.http.v1 import sessions_router as sessions_router
from klaudia_api.interfaces.http.v1 import sheets_router as sheets_router
from klaudia_api.interfaces.http.v1 import spreadsheets_router as spreadsheets_router
from klaudia_api.interfaces.http.v1 import (
    table_authoring_router as table_authoring_router,
)
from klaudia_api.interfaces.http.v1 import tasks_router as tasks_router
from klaudia_api.interfaces.http.v1 import v1_router as v1_router
