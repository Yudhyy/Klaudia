"""Supply application adapters and retain the upload service API."""

from __future__ import annotations

from klaudia_api.exceptions import IngestRejectedError
from klaudia_api.interfaces.http.models.attachment import FileAttachment
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.documents.media.hasher import hash_bytes
from klaudia_api.adapters.documents.media.normalizer import to_canonical_jpg
from klaudia_api.adapters.documents.media.pdf_splitter import (
    iter_pages_as_jpg,
    page_count,
)
from klaudia_api.composition.settings import Settings
from document_intelligence.contracts import QueuedPage, UploadAttachment
from document_intelligence.errors import (
    ExtractionSessionNotFoundError,
    UploadRejectedError,
)
from document_intelligence.ingest import EnqueuedFile as EnqueuedFile
from document_intelligence.ingest import EnqueuedPage as EnqueuedPage
from document_intelligence.ingest import IngestOutcome as IngestOutcome
from document_intelligence.ingest import IngestPage as IngestPage
from document_intelligence.ingest import IngestService as DocumentIngestService
from document_intelligence.ports import (
    DocumentIngestionStore,
    ExtractionCache,
    ExtractionOutcomeStore,
    ExtractionServices,
    IngestLimits,
    MediaProcessing,
    ObjectStore,
    PageOCR,
)
from ledger_core.resources import ResourceNotFoundError


class IngestService(DocumentIngestService):
    """Bind current API dependencies and translate document errors."""

    def __init__(
        self,
        *,
        settings: Settings,
        db: DocumentIngestionStore,
        outcomes: ExtractionOutcomeStore,
        cache: ExtractionCache,
        store: ObjectStore,
        ocr: PageOCR,
        langfuse: LangfuseService | None = None,
    ) -> None:
        """Use the existing constructor for application and test callers."""
        self._langfuse = langfuse
        super().__init__(
            IngestLimits(
                settings.max_pdf_bytes, settings.max_image_bytes, settings.max_pdf_pages
            ),
            ExtractionServices(db, outcomes, cache, store, ocr),
            MediaProcessing(
                to_canonical_jpg, page_count, iter_pages_as_jpg, hash_bytes
            ),
            _dispatch_page,
        )

    async def ingest(
        self,
        attachment: FileAttachment | UploadAttachment,
        *,
        session_id: int,
        user_id: int,
    ) -> IngestOutcome:
        """Run inline extraction and retain the application's rejection type."""
        try:
            return await super().ingest(
                _upload(attachment), session_id=session_id, user_id=user_id
            )
        except UploadRejectedError as error:
            raise IngestRejectedError(str(error), reason=error.reason) from error

    async def enqueue(
        self,
        attachment: FileAttachment | UploadAttachment,
        *,
        session_id: int,
        user_id: int,
    ) -> EnqueuedFile:
        """Queue extraction and retain the application's rejection and access errors."""
        try:
            return await super().enqueue(
                _upload(attachment), session_id=session_id, user_id=user_id
            )
        except UploadRejectedError as error:
            raise IngestRejectedError(str(error), reason=error.reason) from error
        except ExtractionSessionNotFoundError as error:
            raise ResourceNotFoundError(str(error)) from error


def _upload(attachment: FileAttachment | UploadAttachment) -> UploadAttachment:
    """Pass decoded bytes to the package without the HTTP model."""
    return UploadAttachment(
        attachment.filename, attachment.content_type, attachment.data
    )


async def _dispatch_page(job: QueuedPage) -> None:
    """Send the existing Taskiq job payload to the configured broker."""
    from klaudia_api.interfaces.workers.extraction_worker import extract_page_task

    await extract_page_task.kiq(
        user_id=job.user_id, file_id=job.file_id, blob_id=job.blob_id, page=job.page
    )
