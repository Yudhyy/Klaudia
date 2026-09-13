from fastapi import APIRouter

from app.routes.v1.approvals import router as approvals_router
from app.routes.v1.auth import router as auth_router
from app.routes.v1.chat import router as chat_router
from app.routes.v1.health import router as health_router
from app.routes.v1.sessions import router as sessions_router
from app.routes.v1.sheets import router as sheets_router
from app.routes.v1.spreadsheets import router as spreadsheets_router
from app.routes.v1.resources import router as resources_router
from app.routes.v1.tasks import router as tasks_router

from app.routes.v1.table_authoring import router as table_authoring_router

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(health_router)
v1_router.include_router(auth_router)
v1_router.include_router(chat_router)
v1_router.include_router(sessions_router)
v1_router.include_router(sheets_router)
v1_router.include_router(spreadsheets_router)
v1_router.include_router(resources_router)
v1_router.include_router(approvals_router)
v1_router.include_router(tasks_router)
v1_router.include_router(table_authoring_router)

__all__ = ["v1_router"]
