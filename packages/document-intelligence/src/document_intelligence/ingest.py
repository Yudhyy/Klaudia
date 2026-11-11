"""Prepare owned uploads for inline or queued document extraction."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from document_intelligence.contracts import (
    ExtractionScope,
    FileUpload,
    PageOutcome,
    QueuedPage,
    UploadAttachment,
)
from document_intelligence.errors import (
    ExtractionSessionNotFoundError,
    PDFSplitError,
    UnsupportedImageError,
    UploadRejectedError,
)
from document_intelligence.filename import sanitize_filename
from document_intelligence.magic import detect_mime, is_allowed
from document_intelligence.media import (
    SUPPORTED_IMAGE_EXTENSIONS,
    is_pdf,
    is_supported_image,
)
from document_intelligence.ports import (
    DispatchPage,
    ExtractionServices,
    IngestLimits,
    MediaProcessing,
)
from document_intelligence.schema import get_default_extraction, validate_and_merge

logger = logging.getLogger(__name__)


@dataclass
class IngestPage:
    page: int
    page_id: int | None
    extraction: dict[str, Any]
    status: str  # 'extracted' | 'failed' | 'cached'
    cache_layer: str | None  # 'redis' | 'database' | None


@dataclass
class IngestOutcome:
    file_id: int
    file_name: str
    file_type: str  # 'image' | 'pdf'
    pages: list[IngestPage]
    status: str  # 'completed' | 'partial' | 'failed'
    cache_hits: int
    cache_misses: int


@dataclass
class EnqueuedPage:
    page: int
    blob_id: int
    page_hash: str
    cached: bool  # True if already in cache (no task enqueued)


@dataclass
class EnqueuedFile:
    file_id: int
    file_name: str
    file_type: str
    blob_id: int
    pages: list[EnqueuedPage]
    queued: int  # tasks dispatched
    cached_hits: int  # pages already in cache, no task fired


class IngestService:
    """Prepare uploads, extract pages inline or dispatch queued work."""

    def __init__(
        self,
        limits: IngestLimits,
        services: ExtractionServices,
        media: MediaProcessing,
        dispatch: DispatchPage,
    ) -> None:
        """Bind upload limits and the supplied extraction capabilities."""
        self._settings = limits
        self._db = services.documents
        self._cache = services.cache
        self._store = services.store
        self._ocr = services.ocr
        self._outcomes = services.outcomes
        self._media = media
        self._dispatch = dispatch

    async def ingest(
        self,
        attachment: UploadAttachment,
        *,
        session_id: int,
        user_id: int,
    ) -> IngestOutcome:
        attachment = self._sanitize_and_verify(attachment)
        self._guard_attachment(attachment)
        if is_pdf(attachment.filename, attachment.content_type):
            return await self._ingest_pdf(attachment, session_id, user_id)
        if is_supported_image(attachment.filename, attachment.content_type):
            return await self._ingest_image(attachment, session_id, user_id)
        raise UploadRejectedError(
            f"Unsupported attachment type: filename={attachment.filename!r} "
            f"content_type={attachment.content_type!r}",
            reason="unsupported_type",
        )

    def _sanitize_and_verify(self, attachment: UploadAttachment) -> UploadAttachment:
        """Belt-and-suspenders normalization:
            - Sanitize filename (path traversal, control chars, reserved names)
            - Verify magic bytes match a supported type; trust the magic over
              the Content-Type the client sent.
        Returns a new UploadAttachment so the caller's instance stays immutable.
        """
        safe_name = sanitize_filename(attachment.filename)
        magic_mime = detect_mime(attachment.data[:4096])
        # If we can't recognize the bytes, reject — the client lied or sent
        # something we don't process.
        if magic_mime is None or not is_allowed(magic_mime):
            raise UploadRejectedError(
                f"File content does not match a supported type "
                f"(detected={magic_mime!r}, claimed={attachment.content_type!r})",
                reason="bad_magic",
            )
        # Replace the client-provided content_type with the verified one so
        # downstream branches (image vs pdf) are robust against spoofing.
        return UploadAttachment(
            filename=safe_name,
            content_type=magic_mime,
            data=attachment.data,
        )

    def _guard_attachment(self, attachment: UploadAttachment) -> None:
        if not attachment.data:
            raise UploadRejectedError("empty file", reason="empty")
        size = len(attachment.data)
        if is_pdf(attachment.filename, attachment.content_type):
            if size > self._settings.max_pdf_bytes:
                raise UploadRejectedError(
                    f"pdf {size} bytes exceeds limit {self._settings.max_pdf_bytes}",
                    reason="size",
                )
            return
        if is_supported_image(attachment.filename, attachment.content_type):
            if size > self._settings.max_image_bytes:
                raise UploadRejectedError(
                    f"image {size} bytes exceeds limit {self._settings.max_image_bytes}",
                    reason="size",
                )
            return
        raise UploadRejectedError(
            f"unsupported file type: {attachment.content_type!r} "
            f"(supported images: {', '.join(SUPPORTED_IMAGE_EXTENSIONS)} or application/pdf)",
            reason="unsupported_type",
        )

    async def _ingest_image(
        self,
        attachment: UploadAttachment,
        session_id: int,
        user_id: int,
    ) -> IngestOutcome:
        try:
            jpg = self._media.normalize_image(attachment.data)
        except UnsupportedImageError as e:
            return await self._record_failure(
                attachment, session_id, user_id, "image", str(e)
            )

        page_hash = self._media.hash_bytes(jpg)
        # For images the file-level and page-level hash are the same: the
        # canonical JPG IS the only page.
        blob_id, _is_new_blob, blob_minio_key = await self._upsert_blob(
            user_id=user_id,
            blake3_hex=page_hash,
            data=jpg,
            content_type="image/jpeg",
            page_count_=1,
            extension="jpg",
            prefix="blobs",
        )
        # Idempotent page record: page 1 == file
        await self._db.upsert_blob_page(
            blob_id=blob_id,
            page=1,
            page_blake3=page_hash,
            page_minio_key=blob_minio_key,
        )

        file_id = await self._db.create_file(
            FileUpload(session_id, user_id, "image", attachment.filename, 1)
        )
        await self._db.link_metadata_file_blob(file_id, blob_id)

        page_outcome = await self._extract_page(
            user_id=user_id,
            page_hash=page_hash,
            jpg_bytes=jpg,
            page=1,
        )
        await self._persist_page(file_id, page_outcome)

        outcome = IngestOutcome(
            file_id=file_id,
            file_name=attachment.filename,
            file_type="image",
            pages=[page_outcome],
            status="completed" if page_outcome.status != "failed" else "failed",
            cache_hits=1 if page_outcome.cache_layer else 0,
            cache_misses=0 if page_outcome.cache_layer else 1,
        )
        await self._finalize_metadata_file(outcome)
        return outcome

    async def _ingest_pdf(
        self,
        attachment: UploadAttachment,
        session_id: int,
        user_id: int,
    ) -> IngestOutcome:
        try:
            pdf_pages = self._media.count_pdf_pages(attachment.data)
        except PDFSplitError as e:
            return await self._record_failure(
                attachment, session_id, user_id, "pdf", str(e)
            )

        if pdf_pages == 0:
            return await self._record_failure(
                attachment, session_id, user_id, "pdf", "pdf has 0 pages"
            )
        if pdf_pages > self._settings.max_pdf_pages:
            raise UploadRejectedError(
                f"pdf has {pdf_pages} pages; limit is {self._settings.max_pdf_pages}",
                reason="page_count",
            )

        pdf_hash = self._media.hash_bytes(attachment.data)
        blob_id, _is_new_blob, _pdf_minio_key = await self._upsert_blob(
            user_id=user_id,
            blake3_hex=pdf_hash,
            data=attachment.data,
            content_type="application/pdf",
            page_count_=pdf_pages,
            extension="pdf",
            prefix="blobs",
        )

        file_id = await self._db.create_file(
            FileUpload(session_id, user_id, "pdf", attachment.filename, pdf_pages)
        )
        await self._db.link_metadata_file_blob(file_id, blob_id)

        page_outcomes: list[IngestPage] = []
        cache_hits = 0
        cache_misses = 0
        for page_num, jpg in self._media.render_pdf_pages(attachment.data):
            page_hash = self._media.hash_bytes(jpg)
            page_key = self._store.build_blob_key(
                user_id=user_id,
                blake3_hex=page_hash,
                extension="jpg",
                prefix="pages",
            )
            # Per-page MinIO upload only if not already present
            if not await self._store.exists(page_key):
                await self._store.put(page_key, jpg, content_type="image/jpeg")
            await self._db.upsert_blob_page(
                blob_id=blob_id,
                page=page_num,
                page_blake3=page_hash,
                page_minio_key=page_key,
            )

            page_outcome = await self._extract_page(
                user_id=user_id,
                page_hash=page_hash,
                jpg_bytes=jpg,
                page=page_num,
            )
            await self._persist_page(file_id, page_outcome)
            page_outcomes.append(page_outcome)
            if page_outcome.cache_layer:
                cache_hits += 1
            else:
                cache_misses += 1

        succeeded = sum(1 for p in page_outcomes if p.status != "failed")
        if succeeded == len(page_outcomes):
            status = "completed"
        elif succeeded == 0:
            status = "failed"
        else:
            status = "partial"

        outcome = IngestOutcome(
            file_id=file_id,
            file_name=attachment.filename,
            file_type="pdf",
            pages=page_outcomes,
            status=status,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
        )
        await self._finalize_metadata_file(outcome)
        return outcome

    async def _extract_page(
        self,
        *,
        user_id: int,
        page_hash: str,
        jpg_bytes: bytes,
        page: int,
    ) -> IngestPage:
        # L1 (Redis)
        cached = await self._safe_cache_get(user_id, page_hash)
        if cached is not None:
            return IngestPage(
                page=page,
                page_id=None,
                extraction=cached,
                status="cached",
                cache_layer="redis",
            )

        # Persistent database cache
        row = await self._db.get_cached_extraction(user_id, page_hash)
        if row is not None:
            try:
                extraction = json.loads(row["extraction_json"])
            except json.JSONDecodeError:
                logger.warning(
                    "blob_extraction row for page_hash=%s corrupted; re-extracting",
                    page_hash[:12],
                )
            else:
                # Warm L1 for next time
                await self._safe_cache_set(user_id, page_hash, extraction)
                return IngestPage(
                    page=page,
                    page_id=None,
                    extraction=extraction,
                    status="cached",
                    cache_layer="database",
                )

        # Miss → real KIE
        try:
            raw = await self._ocr.extract_from_image(jpg_bytes)
            validated = validate_and_merge(raw)
        except Exception as e:
            logger.error("KIE failed on page %d (hash=%s): %s", page, page_hash[:12], e)
            return IngestPage(
                page=page,
                page_id=None,
                extraction=get_default_extraction(),
                status="failed",
                cache_layer=None,
            )

        await self._db.upsert_extraction(
            user_id=user_id,
            page_blake3=page_hash,
            extraction_json=json.dumps(validated, ensure_ascii=False),
            ocr_model=self._ocr.model_id,
            schema_version=self._ocr.schema_version,
        )
        await self._safe_cache_set(user_id, page_hash, validated)

        return IngestPage(
            page=page,
            page_id=None,
            extraction=validated,
            status="extracted",
            cache_layer=None,
        )

    async def _persist_page(self, file_id: int, outcome: IngestPage) -> None:
        outcome.page_id = await self._db.save_page(
            file_id,
            PageOutcome(
                outcome.page, outcome.status, outcome.extraction, outcome.cache_layer
            ),
        )

    async def _finalize_metadata_file(self, outcome: IngestOutcome) -> None:
        if outcome.status == "completed":
            msg = f"All {len(outcome.pages)} page(s) extracted ({outcome.cache_hits} from cache)"
        elif outcome.status == "partial":
            ok = len(outcome.pages) - sum(
                1 for p in outcome.pages if p.status == "failed"
            )
            msg = f"{ok}/{len(outcome.pages)} page(s) extracted"
        else:
            msg = "All pages failed"
        await self._db.finish_file(outcome.file_id, outcome.status, msg)

    async def _record_failure(
        self,
        attachment: UploadAttachment,
        session_id: int,
        user_id: int,
        file_type: str,
        error_msg: str,
    ) -> IngestOutcome:
        """Persist a failed metadata_file row so the user/agent has visibility.

        Used for pre-flight failures (corrupt PDF, undecodable image) that
        never reach the OCR call.
        """
        file_id = await self._db.create_file(
            FileUpload(
                session_id,
                user_id,
                file_type,
                attachment.filename,
                0,
                "failed",
                error_msg,
            )
        )
        return IngestOutcome(
            file_id=file_id,
            file_name=attachment.filename,
            file_type=file_type,
            pages=[],
            status="failed",
            cache_hits=0,
            cache_misses=0,
        )

    async def _upsert_blob(
        self,
        *,
        user_id: int,
        blake3_hex: str,
        data: bytes,
        content_type: str,
        page_count_: int,
        extension: str,
        prefix: str,
    ) -> tuple[int, bool, str]:
        """Insert file_blob if missing, upload to MinIO if missing.

        Returns (blob_id, is_new, minio_key). Idempotent on hash collision.
        """
        existing = await self._db.find_blob(user_id, blake3_hex)
        if existing is not None:
            # MinIO put is content-addressed; skip if hash row already known.
            return existing["blob_id"], False, existing["minio_key"]

        key = self._store.build_blob_key(
            user_id=user_id,
            blake3_hex=blake3_hex,
            extension=extension,
            prefix=prefix,
        )
        if not await self._store.exists(key):
            await self._store.put(key, data, content_type=content_type)

        blob_id = await self._db.insert_blob(
            user_id=user_id,
            blake3=blake3_hex,
            minio_key=key,
            content_type=content_type,
            size_bytes=len(data),
            page_count=page_count_,
        )
        return blob_id, True, key

    async def _safe_cache_get(
        self, user_id: int, page_hash: str
    ) -> dict[str, Any] | None:
        try:
            return await self._cache.get_extraction(user_id, page_hash)
        except Exception as e:
            logger.warning("Redis cache GET failed: %s (continuing)", e)
            return None

    async def _safe_cache_set(
        self, user_id: int, page_hash: str, extraction: dict[str, Any]
    ) -> None:
        try:
            await self._cache.set_extraction(user_id, page_hash, extraction)
        except Exception as e:
            logger.warning("Redis cache SET failed: %s (continuing)", e)

    async def enqueue(
        self,
        attachment: UploadAttachment,
        *,
        session_id: int,
        user_id: int,
    ) -> EnqueuedFile:
        """Same as ingest() but skips inline OCR — fires a Taskiq task per
        cache-miss page. Cache hits are persisted directly (no task) so the
        orchestrator can stream them as immediate results.
        """
        if await self._db.get_session_owner(session_id) != user_id:
            raise ExtractionSessionNotFoundError("Extraction session not found")
        attachment = self._sanitize_and_verify(attachment)
        self._guard_attachment(attachment)
        if is_pdf(attachment.filename, attachment.content_type):
            blob_id, file_id, page_specs = await self._prepare_pdf(
                attachment, session_id, user_id
            )
            file_type = "pdf"
        elif is_supported_image(attachment.filename, attachment.content_type):
            blob_id, file_id, page_specs = await self._prepare_image(
                attachment, session_id, user_id
            )
            file_type = "image"
        else:
            raise UploadRejectedError(
                f"Unsupported attachment type: filename={attachment.filename!r} "
                f"content_type={attachment.content_type!r}",
                reason="unsupported_type",
            )

        queued = 0
        cached_hits = 0
        page_results: list[EnqueuedPage] = []

        for page_num, page_hash in page_specs:
            cached = await self._page_already_cached(user_id, page_hash)
            if cached is not None:
                # Persist to LLM-visible pages table immediately; warm L1 if
                # the hit was from L2.
                await self._outcomes.save(
                    ExtractionScope(user_id, session_id, file_id),
                    PageOutcome(
                        page=page_num,
                        extraction=cached["extraction"],
                        status="cached",
                        cache_layer=cached["layer"],
                    ),
                )
                if cached["layer"] == "database":
                    await self._safe_cache_set(user_id, page_hash, cached["extraction"])
                cached_hits += 1
                page_results.append(
                    EnqueuedPage(
                        page=page_num,
                        blob_id=blob_id,
                        page_hash=page_hash,
                        cached=True,
                    )
                )
                continue

            await self._dispatch(QueuedPage(user_id, file_id, blob_id, page_num))
            queued += 1
            page_results.append(
                EnqueuedPage(
                    page=page_num,
                    blob_id=blob_id,
                    page_hash=page_hash,
                    cached=False,
                )
            )

        return EnqueuedFile(
            file_id=file_id,
            file_name=attachment.filename,
            file_type=file_type,
            blob_id=blob_id,
            pages=page_results,
            queued=queued,
            cached_hits=cached_hits,
        )

    async def _prepare_image(
        self,
        attachment: UploadAttachment,
        session_id: int,
        user_id: int,
    ) -> tuple[int, int, list[tuple[int, str]]]:
        """Image branch of enqueue — does everything except OCR + final status."""
        try:
            jpg = self._media.normalize_image(attachment.data)
        except UnsupportedImageError as e:
            file_id = await self._record_failure_row(
                attachment, session_id, user_id, "image", str(e)
            )
            raise UploadRejectedError(f"undecodable image: {e}", reason="decode")

        page_hash = self._media.hash_bytes(jpg)
        blob_id, _, blob_key = await self._upsert_blob(
            user_id=user_id,
            blake3_hex=page_hash,
            data=jpg,
            content_type="image/jpeg",
            page_count_=1,
            extension="jpg",
            prefix="blobs",
        )
        await self._db.upsert_blob_page(
            blob_id=blob_id,
            page=1,
            page_blake3=page_hash,
            page_minio_key=blob_key,
        )
        file_id = await self._db.create_file(
            FileUpload(session_id, user_id, "image", attachment.filename, 1)
        )
        await self._db.link_metadata_file_blob(file_id, blob_id)
        return blob_id, file_id, [(1, page_hash)]

    async def _prepare_pdf(
        self,
        attachment: UploadAttachment,
        session_id: int,
        user_id: int,
    ) -> tuple[int, int, list[tuple[int, str]]]:
        try:
            pdf_pages = self._media.count_pdf_pages(attachment.data)
        except PDFSplitError as e:
            await self._record_failure_row(
                attachment, session_id, user_id, "pdf", str(e)
            )
            raise UploadRejectedError(f"unreadable pdf: {e}", reason="decode")
        if pdf_pages == 0:
            raise UploadRejectedError("pdf has 0 pages", reason="empty")
        if pdf_pages > self._settings.max_pdf_pages:
            raise UploadRejectedError(
                f"pdf has {pdf_pages} pages; limit is {self._settings.max_pdf_pages}",
                reason="page_count",
            )

        pdf_hash = self._media.hash_bytes(attachment.data)
        blob_id, _, _ = await self._upsert_blob(
            user_id=user_id,
            blake3_hex=pdf_hash,
            data=attachment.data,
            content_type="application/pdf",
            page_count_=pdf_pages,
            extension="pdf",
            prefix="blobs",
        )
        file_id = await self._db.create_file(
            FileUpload(session_id, user_id, "pdf", attachment.filename, pdf_pages)
        )
        await self._db.link_metadata_file_blob(file_id, blob_id)

        page_specs: list[tuple[int, str]] = []
        for page_num, jpg in self._media.render_pdf_pages(attachment.data):
            page_hash = self._media.hash_bytes(jpg)
            page_key = self._store.build_blob_key(
                user_id=user_id,
                blake3_hex=page_hash,
                extension="jpg",
                prefix="pages",
            )
            if not await self._store.exists(page_key):
                await self._store.put(page_key, jpg, content_type="image/jpeg")
            await self._db.upsert_blob_page(
                blob_id=blob_id,
                page=page_num,
                page_blake3=page_hash,
                page_minio_key=page_key,
            )
            page_specs.append((page_num, page_hash))

        return blob_id, file_id, page_specs

    async def _page_already_cached(
        self, user_id: int, page_hash: str
    ) -> dict[str, Any] | None:
        """Return a Redis or database extraction cache hit."""
        cached = await self._safe_cache_get(user_id, page_hash)
        if cached is not None:
            return {"layer": "redis", "extraction": cached}
        row = await self._db.get_cached_extraction(user_id, page_hash)
        if row is not None:
            try:
                return {
                    "layer": "database",
                    "extraction": json.loads(row["extraction_json"]),
                }
            except json.JSONDecodeError:
                return None
        return None

    async def _record_failure_row(
        self,
        attachment: UploadAttachment,
        session_id: int,
        user_id: int,
        file_type: str,
        error_msg: str,
    ) -> int:
        return await self._db.create_file(
            FileUpload(
                session_id,
                user_id,
                file_type,
                attachment.filename,
                0,
                "failed",
                error_msg,
            )
        )
