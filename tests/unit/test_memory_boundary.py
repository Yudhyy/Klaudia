"""Memory callers use owned contracts and connection-bound reads."""

import json
from unittest.mock import AsyncMock

import pytest

from app.adapters.agent.checkpoints import check_saved_access
from app.adapters.persistence.memory import PostgresMemoryReader
from app.services.memory.contracts import DocumentPath, MemoryDocument
from app.services.memory.ports import DocumentReader
from ledger_core.errors import RevisionConflictError


def saved_reconciliation():
    """Build the existing saved policy evidence format.

    Returns:
        Original owner, input and checkpoint with policy revision three.
    """
    return {
        "user_id": 42,
        "input_payload": json.dumps({"turn": {"document_ids": []}}),
        "checkpoint": json.dumps(
            {
                "evidence": [
                    [
                        "reconcile_with_policy",
                        {},
                        json.dumps({"sources": [], "policy_evidence": {"revision": 3}}),
                    ]
                ]
            }
        ),
    }


@pytest.mark.parametrize("status", ["missing", "active", "deleted"])
async def test_replay_reads_policy_through_the_owner_scoped_interface(status):
    """Only the same active revision can supply saved reconciliation evidence."""
    documents = AsyncMock(spec=DocumentReader)
    documents.read.return_value = MemoryDocument(
        path=DocumentPath.ACCOUNTING_POLICY, status=status, revision=3
    )
    connection = AsyncMock()
    connection.fetchval.return_value = 0
    if status == "active":
        await check_saved_access(connection, saved_reconciliation(), documents)
    else:
        with pytest.raises(RevisionConflictError, match="Saved reconciliation policy"):
            await check_saved_access(connection, saved_reconciliation(), documents)
    documents.read.assert_awaited_once_with(42, DocumentPath.ACCOUNTING_POLICY)
    assert all(
        "memory_document" not in call.args[0]
        for call in connection.fetchval.await_args_list
    )


async def test_replay_rejects_a_new_active_policy_revision():
    """Restoring the same prose cannot restore the old policy revision."""
    documents = AsyncMock(spec=DocumentReader)
    documents.read.return_value = MemoryDocument(
        path=DocumentPath.ACCOUNTING_POLICY, status="active", revision=5
    )
    with pytest.raises(RevisionConflictError, match="Saved reconciliation policy"):
        await check_saved_access(AsyncMock(), saved_reconciliation(), documents)


async def test_replay_does_not_hide_an_unavailable_policy_reader():
    """A read failure stops replay before another source or model call."""
    documents = AsyncMock(spec=DocumentReader)
    documents.read.side_effect = ConnectionError("Policy read unavailable")
    connection = AsyncMock()
    with pytest.raises(ConnectionError, match="Policy read unavailable"):
        await check_saved_access(connection, saved_reconciliation(), documents)
    connection.fetchval.assert_not_awaited()


async def test_memory_reader_uses_the_supplied_task_connection():
    """Read on the supplied connection without acquiring another pool slot."""
    connection = AsyncMock()
    connection.fetchrow.return_value = {
        "path": "/accounting-policy.md",
        "revision": 3,
        "status": "active",
        "content": "Policy prose",
        "actor_id": 42,
        "updated_at": None,
        "source_note": "Human edit",
        "policy": None,
    }
    document = await PostgresMemoryReader(connection).read(
        42, DocumentPath.ACCOUNTING_POLICY
    )
    assert document.revision == 3
    assert connection.fetchrow.await_args.args[1:] == (
        42,
        DocumentPath.ACCOUNTING_POLICY,
    )
    connection.acquire.assert_not_called()


async def test_memory_reader_preserves_missing_document_status():
    """An absent row has revision zero and no content."""
    connection = AsyncMock()
    connection.fetchrow.return_value = None
    document = await PostgresMemoryReader(connection).read(42, DocumentPath.PREFERENCES)
    assert document == MemoryDocument(path=DocumentPath.PREFERENCES)


def test_old_memory_imports_alias_the_canonical_adapters_and_error():
    """Retain existing caller imports without a second implementation."""
    from app.adapters.ledger.reconciliation import PolicyReconciler
    from app.adapters.persistence.memory import PostgresMemoryDocumentStore
    from app.services.memory import reconciliation, store
    from app.services.memory.ports import DocumentConflict

    assert store.MemoryDocumentStore is PostgresMemoryDocumentStore
    assert store.DocumentConflict is DocumentConflict
    assert reconciliation.PolicyReconciler is PolicyReconciler
