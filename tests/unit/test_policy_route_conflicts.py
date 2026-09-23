"""Policy revision conflicts remain explicit at authenticated HTTP boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.routes.v1.chat import router as chat_router
from app.routes.v1.tasks import router as task_router
from ledger.errors import RevisionConflictError
from tests.integration.api.test_memory_routes import headers


@pytest.mark.parametrize(
    "route,payload",
    [
        ("/v1/chat", {"messages": [{"role": "user", "content": "Reconcile"}]}),
        ("/v1/tasks/task:example/resume", None),
    ],
)
async def test_policy_conflict_returns_http_409(route, payload):
    """Return a conflict rather than an internal error for stale saved policy."""
    orchestrator = AsyncMock()
    orchestrator.process.side_effect = RevisionConflictError(
        "Saved reconciliation policy changed"
    )
    orchestrator.resume_task.side_effect = RevisionConflictError(
        "Saved reconciliation policy changed"
    )
    app = FastAPI()
    app.state.orchestrator = orchestrator
    app.state.container = SimpleNamespace(tasks=object())
    app.include_router(chat_router, prefix="/v1")
    app.include_router(task_router, prefix="/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(route, json=payload, headers=headers())
    assert response.status_code == 409
    assert response.json()["detail"] == "Saved reconciliation policy changed"
