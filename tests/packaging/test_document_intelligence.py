"""Exercise inline and queued extraction through an isolated installed wheel."""

from tests.packaging.test_ledger_core import install_agent_packages, probe


def test_installed_document_ingestion_and_queued_processing(tmp_path):
    """Use supplied capabilities to dispatch, process and save document pages."""
    python = install_agent_packages(tmp_path, ("packages/document-intelligence",))
    output = probe(
        python,
        '''
import asyncio
from importlib.metadata import distributions
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from document_intelligence.contracts import ExtractionScope, QueuedPage, UploadAttachment
from document_intelligence.ingest import IngestService
from document_intelligence.ports import (
    DocumentIngestionStore, ExtractionCache, ExtractionOutcomeStore,
    ExtractionServices, IngestLimits, MediaProcessing, ObjectStore,
)
from document_intelligence.queued import QueuedExtraction

assert {package.metadata['Name'] for package in distributions()} == {'document-intelligence'}

async def extract():
    """Run the upload and worker use cases without an application or broker."""
    documents = AsyncMock(spec=DocumentIngestionStore)
    documents.get_session_owner.return_value = 42
    documents.find_blob.return_value = None
    documents.insert_blob.return_value = 9
    documents.create_file.return_value = 11
    documents.get_cached_extraction.return_value = None
    documents.save_page.return_value = 13
    scope = ExtractionScope(42, 7, 11)
    outcomes = AsyncMock(spec=ExtractionOutcomeStore)
    outcomes.resolve_page.return_value = (
        scope, {'page_blake3': 'page-hash', 'page_minio_key': 'owner/page.jpg'},
    )
    cache = AsyncMock(spec=ExtractionCache)
    cache.get_extraction.return_value = None
    objects = Mock(spec=ObjectStore)
    objects.build_blob_key.return_value = 'owner/page.jpg'
    objects.exists.return_value = True
    objects.get.return_value = b'normalized'
    ocr = SimpleNamespace(
        extract_from_image=AsyncMock(return_value={'info': {'store_name': 'Owned shop'}}),
        model_id='test-ocr', schema_version='1',
    )
    services = ExtractionServices(documents, outcomes, cache, objects, ocr)
    media = MediaProcessing(
        Mock(return_value=b'normalized'), Mock(), Mock(), Mock(return_value='page-hash'),
    )
    dispatch = AsyncMock()
    upload = UploadAttachment('../receipt.jpg', 'text/plain', bytes.fromhex('ffd8ff') + b'image')
    ingest = IngestService(IngestLimits(1024, 1024, 5), services, media, dispatch)
    inline = await ingest.ingest(upload, session_id=7, user_id=42)
    assert (inline.file_id, inline.file_name, inline.status) == (11, 'receipt.jpg', 'completed')
    assert inline.pages[0].extraction['info']['store_name'] == 'Owned shop'
    dispatch.assert_not_awaited()
    ocr.extract_from_image.reset_mock()

    enqueued = await ingest.enqueue(upload, session_id=7, user_id=42)
    job = QueuedPage(42, 11, 9, 1)
    assert (enqueued.queued, enqueued.cached_hits) == (1, 0)
    dispatch.assert_awaited_once_with(job)
    ocr.extract_from_image.assert_not_awaited()

    async def check_saved_page(file_id, page):
        """Observe the saved result before the worker sends progress."""
        outcomes.save.assert_awaited_once_with(scope, page)
        assert file_id == scope.file_id
        assert page.extraction['info']['store_name'] == 'Owned shop'

    publish = AsyncMock(side_effect=check_saved_page)
    assert await QueuedExtraction(services, publish).run(job) == {'status': 'extracted'}
    assert publish.await_count == 1
    outcomes.resolve_page.assert_awaited_once_with(job)
    ocr.extract_from_image.assert_awaited_once_with(b'normalized')

asyncio.run(extract())
print('installed document ingestion and queued processing verified')
''',
    )
    assert output == "installed document ingestion and queued processing verified"
