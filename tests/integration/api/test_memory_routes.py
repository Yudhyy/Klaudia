"""Authenticated context editing through signed-token HTTP requests."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.routes.v1.memory import router
from app.services.auth.tokens import create_access_token
from app.services.memory.store import MemoryDocumentStore
from config.settings import get_settings


@pytest.fixture
async def memory_app(postgres_db):
    """Connect the real store and API to isolated owners."""
    await postgres_db.execute(
        'INSERT INTO "user" (user_id, username, email, password_hash) VALUES (2, $1, $2, $3)',
        ("second", "second@example.test", "fixture"),
    )
    store = MemoryDocumentStore(postgres_db.pool)
    await store.initialize()
    app = FastAPI()
    app.state.container = SimpleNamespace(memory_documents=store)
    app.include_router(router, prefix="/v1")
    return app


@pytest.fixture
async def memory_client(memory_app):
    """Expose HTTP requests against the isolated context application."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=memory_app), base_url="http://test"
    ) as client:
        yield client


def headers(owner=1):
    """Sign the actual authentication token for an isolated owner."""
    token = create_access_token(owner, secret=get_settings().jwt_secret)
    return {"Authorization": f"Bearer {token}"}


async def test_memory_routes_require_authentication(memory_client):
    """Reject all document access without a signed identity."""
    for method in ("GET", "PUT", "DELETE"):
        response = await memory_client.request(method, "/v1/memory/preferences.md")
        assert response.status_code == 401


async def test_edit_read_delete_restore(memory_client):
    """Expose revisions and return conflicts for stale edits."""
    url = "/v1/memory/preferences.md"
    created = await memory_client.put(
        url,
        headers=headers(),
        json={
            "expected_revision": 0,
            "content": "Use USD",
            "source_note": "Explicit choice",
        },
    )
    assert created.status_code == 200
    assert created.json()["actor_id"] == 1
    assert created.json()["revision"] == 1
    assert (await memory_client.get(url, headers=headers())).json()[
        "content"
    ] == "Use USD"
    assert (await memory_client.get(url, headers=headers(2))).json()[
        "status"
    ] == "missing"
    stale = await memory_client.put(
        url, headers=headers(), json={"expected_revision": 0, "content": "Stale"}
    )
    assert stale.status_code == 409
    deleted = await memory_client.delete(
        url, headers=headers(), params={"expected_revision": 1}
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["content"] is None
    restored = await memory_client.put(
        url, headers=headers(), json={"expected_revision": 2, "content": "Use EUR"}
    )
    assert restored.json()["revision"] == 3


async def test_reject_client_scope_and_unknown_paths(memory_client):
    """Keep identity and the path allowlist outside caller control."""
    response = await memory_client.put(
        "/v1/memory/preferences.md",
        headers=headers(),
        json={"expected_revision": 0, "content": "Text", "user_id": 2},
    )
    assert response.status_code == 422
    assert (
        await memory_client.get("/v1/memory/secrets.md", headers=headers())
    ).status_code == 422
    assert (
        await memory_client.delete("/v1/memory/preferences.md", headers=headers())
    ).status_code == 422


async def test_memory_unavailable_is_explicit(memory_client, memory_app):
    """Do not substitute empty context when the store is unavailable."""
    memory_app.state.container.memory_documents = None
    response = await memory_client.get("/v1/memory/preferences.md", headers=headers())
    assert response.status_code == 503


async def test_reject_revision_outside_database_range(memory_client):
    """Reject oversized revisions at the HTTP boundary."""
    url = "/v1/memory/preferences.md"
    oversized = 2**63
    edited = await memory_client.put(
        url, headers=headers(), json={"expected_revision": oversized, "content": "Text"}
    )
    assert edited.status_code == 422
    deleted = await memory_client.delete(
        url, headers=headers(), params={"expected_revision": oversized}
    )
    assert deleted.status_code == 422


async def test_policy_http_scope_and_exact_roundtrip(memory_client):
    """Keep typed policy owner-scoped and reject policy on preference paths."""
    from tests.unit.test_accounting_policy import policy_fields

    payload = {"expected_revision": 0, "content": "Policy", "policy": policy_fields()}
    rejected = await memory_client.put(
        "/v1/memory/preferences.md", headers=headers(), json=payload
    )
    assert rejected.status_code == 422
    saved = await memory_client.put(
        "/v1/memory/accounting-policy.md", headers=headers(), json=payload
    )
    assert saved.status_code == 200
    assert saved.json()["policy"] == policy_fields()
    other = await memory_client.get(
        "/v1/memory/accounting-policy.md", headers=headers(2)
    )
    assert other.json()["policy"] is None
    stale = await memory_client.put(
        "/v1/memory/accounting-policy.md", headers=headers(), json=payload
    )
    assert stale.status_code == 409


def preference_import(**changes):
    """Describe a user-reviewed preference import with explicit source records."""
    return {
        "expected_revision": 0,
        "content": "Show dates as YYYY-MM-DD.",
        "source_ids": ["mem-001", "mem-002"],
        "reviewed_preferences_only": True,
        **changes,
    }


async def test_reviewed_preference_import_retains_provenance_and_conflicts(
    memory_client, memory_app
):
    """Import only into the signed owner's preferences and reject duplicate stale writes."""
    payload = preference_import()
    url = "/v1/memory/imports/preferences"
    response = await memory_client.post(url, headers=headers(), json=payload)
    assert response.status_code == 200
    saved = response.json()
    assert saved["content"] == payload["content"]
    assert (
        saved["source_note"] == 'Reviewed mem0 preference import: ["mem-001","mem-002"]'
    )
    assert saved["actor_id"] == 1
    assert saved["policy"] is None
    assert saved["revision"] == 1
    assert (
        await memory_client.post(url, headers=headers(), json=payload)
    ).status_code == 409
    other = await memory_client.get("/v1/memory/preferences.md", headers=headers(2))
    assert other.json()["status"] == "missing"
    assert (
        await memory_client.get("/v1/memory/accounting-policy.md", headers=headers())
    ).json()["status"] == "missing"


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewed_preferences_only": False},
        {"reviewed_preferences_only": 1},
        {"source_ids": []},
        {"source_ids": ["same", "same"]},
        {"source_ids": ["x" * 65]},
        {"source_ids": ["bad\nrecord"]},
        {"content": "x" * 8193},
        {"user_id": 2},
        {"policy": {}},
    ],
)
async def test_preference_import_rejects_unreviewed_or_unbounded_input(
    memory_client, changes
):
    """Reject absent review, ambiguous provenance, forged scope and excessive text."""
    response = await memory_client.post(
        "/v1/memory/imports/preferences",
        headers=headers(),
        json=preference_import(**changes),
    )
    assert response.status_code == 422


async def test_preference_import_requires_auth_and_supports_checked_rollback(
    memory_client,
):
    """Keep existing preferences recoverable through an explicit revision-checked edit."""
    url = "/v1/memory/preferences.md"
    original = {
        "expected_revision": 0,
        "content": "Original preference",
        "source_note": "User choice",
    }
    assert (
        await memory_client.put(url, headers=headers(), json=original)
    ).status_code == 200
    imported = await memory_client.post(
        "/v1/memory/imports/preferences",
        headers=headers(),
        json=preference_import(
            expected_revision=1, content="Original preference\nShow ISO dates."
        ),
    )
    assert imported.status_code == 200
    assert imported.json()["revision"] == 2
    assert (
        await memory_client.post(
            "/v1/memory/imports/preferences", json=preference_import()
        )
    ).status_code == 401
    original["expected_revision"] = 2
    restored = await memory_client.put(url, headers=headers(), json=original)
    assert restored.status_code == 200
    assert restored.json()["content"] == "Original preference"
    assert restored.json()["revision"] == 3


async def test_imported_preferences_match_agent_reads_and_retained_history(
    memory_client, memory_app
):
    """Preserve exact imported text and provenance through the agent read boundary."""
    from app.services.memory.contracts import DocumentPath
    from app.services.memory.tools import MemoryTools

    payload = preference_import(content="Use ISO dates. Keep account labels verbatim.")
    response = await memory_client.post(
        "/v1/memory/imports/preferences", headers=headers(), json=payload
    )
    assert response.status_code == 200
    store = memory_app.state.container.memory_documents
    evidence = await MemoryTools(store, 1).read_tool.ainvoke(
        {"path": "/preferences.md"}
    )
    assert evidence["document"] == response.json()
    assert evidence["authority"] == "untrusted_context"
    assert evidence["accounting_validation"] == "not_run"
    assert (await store.read(2, DocumentPath.PREFERENCES)).status == "missing"
