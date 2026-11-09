"""Canonical document and conversation stores retain scope and saved formats."""

import json

import pytest

from app.adapters.persistence.conversations import PostgresConversationStore
from app.adapters.persistence.documents import PostgresDocumentStore
from app.adapters.persistence.tasks import PostgresTaskStore
from app.services.documents.contracts import DocumentPage, FileUpload, PageOutcome
from ledger_core.resources import ResourceNotFoundError
from tests.integration.postgres import POSTGRES_TEST_URL


async def test_canonical_conversation_store_preserves_message_order(postgres_db):
    """Keep recent history descending and session views ascending."""
    conversations = PostgresConversationStore(postgres_db.pool)
    session_id = await conversations.create_session(1, "Storage check")
    first = await conversations.save_message(session_id, 1, "user", "First")
    second = await conversations.save_message(session_id, 1, "assistant", "Second")
    assert first != second
    await conversations.update_session_timestamp(session_id)
    assert await conversations.get_session_owner(session_id) == 1
    assert await conversations.get_session_owner(session_id + 999) is None
    assert [
        row["message_text"]
        for row in await conversations.get_session_messages(session_id)
    ] == ["First", "Second"]
    assert [
        row["message_text"]
        for row in await conversations.get_conversation_history(session_id, 1)
    ] == ["Second"]
    assert [row["session_id"] for row in await conversations.get_sessions(1)] == [
        session_id
    ]
    assert await conversations.get_sessions(999) == []


async def test_document_store_keeps_archive_bounds_and_inline_page_format(postgres_db):
    """Keep literal search, character pagination and cached page messages."""
    documents = PostgresDocumentStore(postgres_db.pool)
    session_id = await postgres_db.create_session(1)
    file_id = await documents.create_file(
        FileUpload(session_id, 1, "image", "receipt%.jpg", 1)
    )
    extraction = {"text": "€" * 17000}
    page_id = await documents.save_page(
        file_id, PageOutcome(1, "cached", extraction, "redis")
    )
    await documents.finish_file(
        file_id, "completed", "All 1 page(s) extracted (1 from cache)"
    )
    rows = await documents.search_documents(1, "%")
    assert [row["file_id"] for row in rows] == [file_id]
    assert await documents.search_documents(1, "missing") == []
    page = await documents.read_document_page(1, DocumentPage(file_id, 1, 8192))
    stored_text = json.dumps(extraction, ensure_ascii=False)
    assert page["extraction_text"] == stored_text[8192:16384]
    assert page["total_characters"] == len(stored_text)
    assert await documents.read_document_page(1, DocumentPage(file_id, 2)) is None
    saved = await postgres_db.fetchone(
        "SELECT status, status_message FROM pages WHERE id=$1", (page_id,)
    )
    assert saved == {"status": "extracted", "status_message": "cached"}
    assert await documents.count_owned_documents(1, [file_id]) == 1
    assert await documents.count_owned_documents(1, []) == 0


@pytest.mark.parametrize("changed_owner", ["file", "session"])
async def test_document_archive_rechecks_both_current_owners(
    postgres_db, changed_owner
):
    """Revoking either relation removes page and metadata access."""
    other = await postgres_db.create_user("other", "other@example.test", "fixture")
    session_id = await postgres_db.create_session(1)
    documents = PostgresDocumentStore(postgres_db.pool)
    file_id = await documents.create_file(
        FileUpload(session_id, 1, "image", "private.jpg", 1)
    )
    await documents.save_page(file_id, PageOutcome(1, "extracted", {"private": "text"}))
    if changed_owner == "file":
        await postgres_db.execute(
            "UPDATE metadata_file SET user_id=$1 WHERE id=$2", (other, file_id)
        )
    else:
        await postgres_db.execute(
            "UPDATE session SET user_id=$1 WHERE session_id=$2", (other, session_id)
        )
    for user_id in (1, other):
        assert await documents.search_documents(user_id) == []
        assert (
            await documents.read_document_page(user_id, DocumentPage(file_id, 1))
            is None
        )
        assert await documents.count_owned_documents(user_id, [file_id]) == 0


async def test_document_replay_reads_use_the_backend_holding_the_task_lock(
    postgres_db, monkeypatch
):
    """Prove saved document checks reuse the locked task backend."""
    session_id = await postgres_db.create_session(1)
    documents = PostgresDocumentStore(postgres_db.pool)
    file_id = await documents.create_file(
        FileUpload(session_id, 1, "image", "receipt.jpg", 1)
    )
    tasks = PostgresTaskStore(POSTGRES_TEST_URL)
    await tasks.connect()
    observed = []
    count = PostgresDocumentStore.count_owned_documents

    async def observed_count(store, user_id, document_ids):
        """Read backend identity while the real owner check uses that connection.

        Args:
            store: Connection-bound document adapter.
            user_id: Original task owner.
            document_ids: Original input documents.

        Returns:
            Current number of owned documents.
        """
        backend = await store._pool.fetchval("SELECT pg_backend_pid()")
        held = await store._pool.fetchval(
            "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted"
        )
        assert held > 0
        observed.append(backend)
        return await count(store, user_id, document_ids)

    monkeypatch.setattr(PostgresDocumentStore, "count_owned_documents", observed_count)
    try:
        task_id = await tasks.create(
            (1, session_id),
            {
                "turn": {
                    "text": "Read receipt",
                    "document_ids": [file_id],
                    "active_workbook_id": None,
                }
            },
        )
        async with tasks.open(1, task_id) as task:
            backend = await task.connection.fetchval("SELECT pg_backend_pid()")
            assert observed == [backend]
        other = await postgres_db.create_user("other", "other@example.test", "fixture")
        await postgres_db.execute(
            "UPDATE metadata_file SET user_id=$1 WHERE id=$2", (other, file_id)
        )
        with pytest.raises(ResourceNotFoundError, match="Saved task source"):
            async with tasks.open(1, task_id):
                pytest.fail("Revoked document reached task execution")
    finally:
        await tasks.close()


async def test_inline_failures_keep_their_saved_status_messages(postgres_db):
    """Keep preflight and OCR failure records visible without stored page facts."""
    session_id = await postgres_db.create_session(1)
    documents = PostgresDocumentStore(postgres_db.pool)
    file_id = await documents.create_file(
        FileUpload(session_id, 1, "pdf", "broken.pdf", 0, "failed", "Unreadable PDF")
    )
    saved = await postgres_db.fetchone(
        "SELECT status, status_message, total_pages FROM metadata_file WHERE id=$1",
        (file_id,),
    )
    assert saved == {
        "status": "failed",
        "status_message": "Unreadable PDF",
        "total_pages": 0,
    }
    image_id = await documents.create_file(
        FileUpload(session_id, 1, "image", "failed.jpg", 1)
    )
    await documents.save_page(image_id, PageOutcome(1, "failed"))
    page = await documents.read_document_page(1, DocumentPage(image_id, 1))
    assert page["status"] == "failed"
    assert page["extraction_text"] is None
