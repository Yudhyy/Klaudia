"""Document and conversation callers use storage interfaces without raw SQL."""

import json
from unittest.mock import AsyncMock
from unittest.mock import Mock
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.adapters.agent.checkpoints import SavedSources, check_saved_access
from app.adapters.persistence.conversations import PostgresConversationStore
from app.services.conversation.store import ConversationStore
from app.services.core.archive_tools import ArchiveReader
from agent_core.agent.ports.errors import (
    ResourceNotFoundError as CapabilityResourceNotFound,
)
from app.services.documents.ports import DocumentArchive
from app.services.documents.ports import DocumentIngestionStore, ExtractionOutcomeStore
from app.services.memory.ports import DocumentReader
from app.services.catalogue.ports import WorkbookAccess
from ledger_core.resources import ResourceNotFoundError


async def test_archive_reader_uses_owner_scoped_document_methods():
    """Keep page bounds and tool evidence outside the persistence adapter."""
    documents = AsyncMock(spec=DocumentArchive)
    documents.search_documents.return_value = [
        {"file_id": number} for number in range(21)
    ]
    documents.read_document_page.return_value = {
        "file_id": 7,
        "page": 2,
        "status": "extracted",
        "extraction_text": "Saved text",
        "total_characters": 20000,
    }
    reader = ArchiveReader(documents, 42)
    found = await reader.search("receipt", 20)
    assert len(found["documents"]) == 20
    assert found["next_offset"] == 40
    documents.search_documents.assert_awaited_once_with(42, "receipt", 20)
    page = await reader.read(7, 2, 8192)
    assert page["offset"] == 8192
    assert page["next_offset"] == 16384
    owner, request = documents.read_document_page.await_args.args
    assert (owner, request.file_id, request.page, request.offset) == (42, 7, 2, 8192)


async def test_archive_reader_keeps_missing_pages_and_read_failures_explicit():
    """Missing documents and unavailable storage must not become empty facts."""
    documents = AsyncMock(spec=DocumentArchive)
    documents.read_document_page.return_value = None
    with pytest.raises(CapabilityResourceNotFound, match="Document page not found"):
        await ArchiveReader(documents, 42).read(7, 1)
    documents.search_documents.side_effect = ConnectionError("Archive unavailable")
    with pytest.raises(ConnectionError, match="Archive unavailable"):
        await ArchiveReader(documents, 42).search()


async def test_conversation_adapter_reuses_the_pool_and_keeps_history_order():
    """Keep the current descending history query and caller-selected bound."""
    pool = AsyncMock()
    pool.fetch.return_value = [{"sender": "user", "message_text": "Saved request"}]
    store: ConversationStore = PostgresConversationStore(pool)
    assert await store.get_conversation_history(7, limit=10) == pool.fetch.return_value
    query, *parameters = pool.fetch.await_args.args
    assert "ORDER BY timestamp DESC" in query
    assert parameters == [7, 10]
    pool.close.assert_not_awaited()
    pool.acquire.assert_not_called()


async def test_conversation_adapter_does_not_hide_failed_recovery_writes():
    """A failed message write prevents operation recovery from claiming success."""
    pool = AsyncMock()
    pool.execute.side_effect = ConnectionError("History unavailable")
    with pytest.raises(ConnectionError, match="History unavailable"):
        await PostgresConversationStore(pool).save_message(
            7, 42, "assistant", "Receipt"
        )


@pytest.mark.parametrize("owned", [0, 1])
async def test_replay_checks_document_access_through_the_document_interface(owned):
    """A saved input cannot bypass a later change in document/session ownership."""
    documents = AsyncMock(spec=DocumentArchive)
    documents.count_owned_documents.return_value = owned
    workbooks = AsyncMock(spec=WorkbookAccess)
    workbooks.count_owned_workbooks.return_value = 0
    record = {
        "user_id": 42,
        "input_payload": json.dumps({"turn": {"document_ids": [7]}}),
        "checkpoint": None,
    }
    sources = SavedSources(AsyncMock(spec=DocumentReader), documents, workbooks)
    if owned:
        await check_saved_access(record, sources)
    else:
        with pytest.raises(ResourceNotFoundError, match="Saved task source"):
            await check_saved_access(record, sources)
    documents.count_owned_documents.assert_awaited_once_with(42, [7])
    workbooks.count_owned_workbooks.assert_awaited_once_with(42, [])


async def test_inline_ingestion_uses_only_the_document_storage_interface():
    """Persist an image and its exact page outcome without a raw query method."""
    from app.models.attachment import FileAttachment
    from app.services.extraction.ingest import IngestService
    from config.settings import Settings

    documents = AsyncMock(spec=DocumentIngestionStore)
    documents.find_blob.return_value = None
    documents.insert_blob.return_value = 9
    documents.create_file.return_value = 11
    documents.save_page.return_value = 13
    documents.get_cached_extraction.return_value = None
    cache = AsyncMock()
    cache.get_extraction.return_value = None
    store = Mock()
    store.build_blob_key.return_value = "owner/blob.jpg"
    store.exists = AsyncMock(return_value=True)
    ocr = SimpleNamespace(
        extract_from_image=AsyncMock(
            return_value={"info": {"store_name": "Saved shop"}}
        ),
        model_id="test",
        schema_version="1",
    )
    service = IngestService(
        settings=Settings(_env_file=None),
        db=documents,
        outcomes=AsyncMock(spec=ExtractionOutcomeStore),
        cache=cache,
        store=store,
        ocr=ocr,
    )
    image = Path(__file__).parents[2] / "sample-data/receipt/001-receipt.jpeg"
    outcome = await service.ingest(
        FileAttachment(
            filename="receipt.jpeg", content_type="image/jpeg", data=image.read_bytes()
        ),
        session_id=7,
        user_id=42,
    )
    assert (outcome.file_id, outcome.pages[0].page_id, outcome.status) == (
        11,
        13,
        "completed",
    )
    upload = documents.create_file.await_args.args[0]
    assert (upload.user_id, upload.session_id, upload.total_pages) == (42, 7, 1)
    file_id, page = documents.save_page.await_args.args
    assert file_id == 11
    assert page.extraction["info"]["store_name"] == "Saved shop"
    documents.finish_file.assert_awaited_once_with(
        11, "completed", "All 1 page(s) extracted (0 from cache)"
    )


def test_legacy_database_and_outcome_imports_reuse_canonical_types():
    """Keep supported import and constructor behavior without duplicate SQL."""
    from app.adapters.persistence.application import ApplicationDatabase
    from app.adapters.persistence.extraction_outcomes import PostgresExtractionOutcomes
    from app.services.extraction.infra.db_client import AppDBClient
    from app.services.extraction.outcomes import ExtractionOutcomes, FileOutcome
    from app.services.documents.contracts import FileOutcome as DocumentOutcome

    database = SimpleNamespace(pool=Mock())
    outcomes = ExtractionOutcomes(database, max_pages=7)
    assert AppDBClient is ApplicationDatabase
    assert FileOutcome is DocumentOutcome
    assert isinstance(outcomes, PostgresExtractionOutcomes)
    assert outcomes._pool is database.pool
