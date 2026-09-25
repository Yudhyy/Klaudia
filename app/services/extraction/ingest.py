"""IngestService: per-file dedup + extraction pipeline (synchronous).

Flow per attachment:
    1. Validate (mime, size, type) — cheap pre-flight, no GPU cost
    2. Branch on type:
         image → normalize to JPG → hash → MinIO put → 1 page
         pdf   → hash original PDF → MinIO put → render N pages → each page:
                 normalize → hash page → check caches → OCR if miss → persist
    3. Result: ExtractionResult with per-page extraction JSON, ready to feed
       the main agent through the existing context builder.

Hash + MinIO key never leave this module. ExtractionResult only carries
metadata_file_id, page numbers, and validated extraction JSON.

"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.exceptions import IngestRejectedError, OCRError
from app.models.attachment import FileAttachment
from app.services.core.observability import LangfuseService
from app.services.extraction.agents.config import get_default_extraction
from app.services.extraction.agents.schema import validate_and_merge
from app.services.extraction.infra.db_client import AppDBClient
from app.services.extraction.infra.dedup_cache import DedupCache
from app.services.extraction.infra.filename import sanitize_filename
from app.services.extraction.infra.hasher import hash_bytes
from app.services.extraction.infra.magic import detect_mime, is_allowed
from app.services.extraction.infra.normalizer import (
    SUPPORTED_IMAGE_EXTENSIONS,
    UnsupportedImageError,
    is_pdf,
    is_supported_image,
    to_canonical_jpg,
)
from app.services.extraction.infra.object_store import MinIOClient
from app.services.extraction.infra.kie_client import KIEClient
from app.services.extraction.infra.pdf_splitter import (
    PDFSplitError,
    iter_pages_as_jpg,
    page_count,
)
from config.settings import Settings

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
    """Synchronous dedup + extraction. Workers can also call into the
    page-level methods (extract_page) once we move to the queue.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        db: AppDBClient,
        cache: DedupCache,
        store: MinIOClient,
        ocr: KIEClient,
        langfuse: LangfuseService | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._cache = cache
        self._store = store
        self._ocr = ocr
        self._langfuse = langfuse

    async def ingest(
        self,
        attachment: FileAttachment,
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
        raise IngestRejectedError(
            f"Unsupported attachment type: filename={attachment.filename!r} "
            f"content_type={attachment.content_type!r}",
            reason="unsupported_type",
        )

    def _sanitize_and_verify(self, attachment: FileAttachment) -> FileAttachment:
        """Belt-and-suspenders normalization:
            - Sanitize filename (path traversal, control chars, reserved names)
            - Verify magic bytes match a supported type; trust the magic over
              the Content-Type the client sent.
        Returns a new FileAttachment so the caller's instance stays immutable.
        """
        safe_name = sanitize_filename(attachment.filename)
        magic_mime = detect_mime(attachment.data[:4096])
        # If we can't recognize the bytes, reject — the client lied or sent
        # something we don't process.
        if magic_mime is None or not is_allowed(magic_mime):
            raise IngestRejectedError(
                f"File content does not match a supported type "
                f"(detected={magic_mime!r}, claimed={attachment.content_type!r})",
                reason="bad_magic",
            )
        # Replace the client-provided content_type with the verified one so
        # downstream branches (image vs pdf) are robust against spoofing.
        return FileAttachment(
            filename=safe_name,
            content_type=magic_mime,
            data=attachment.data,
        )

    def _guard_attachment(self, attachment: FileAttachment) -> None:
        if not attachment.data:
            raise IngestRejectedError("empty file", reason="empty")
        size = len(attachment.data)
        if is_pdf(attachment.filename, attachment.content_type):
            if size > self._settings.max_pdf_bytes:
                raise IngestRejectedError(
                    f"pdf {size} bytes exceeds limit {self._settings.max_pdf_bytes}",
                    reason="size",
                )
            return
        if is_supported_image(attachment.filename, attachment.content_type):
            if size > self._settings.max_image_bytes:
                raise IngestRejectedError(
                    f"image {size} bytes exceeds limit {self._settings.max_image_bytes}",
                    reason="size",
                )
            return
        raise IngestRejectedError(
            f"unsupported file type: {attachment.content_type!r} "
            f"(supported images: {', '.join(SUPPORTED_IMAGE_EXTENSIONS)} or application/pdf)",
            reason="unsupported_type",
        )

    async def _ingest_image(
        self,
        attachment: FileAttachment,
        session_id: int,
        user_id: int,
    ) -> IngestOutcome:
        try:
            jpg = to_canonical_jpg(attachment.data)
        except UnsupportedImageError as e:
            return await self._record_failure(
                attachment, session_id, user_id, "image", str(e)
            )

        page_hash = hash_bytes(jpg)
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

        file_id = int(
            await self._db.fetchval(
                """
                INSERT INTO metadata_file
                    (session_id, user_id, type, file_name, total_pages, status)
                VALUES ($1, $2, 'image', $3, 1, 'pending')
                RETURNING id
                """,
                (session_id, user_id, attachment.filename),
            )
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
        attachment: FileAttachment,
        session_id: int,
        user_id: int,
    ) -> IngestOutcome:
        try:
            pdf_pages = page_count(attachment.data)
        except PDFSplitError as e:
            return await self._record_failure(
                attachment, session_id, user_id, "pdf", str(e)
            )

        if pdf_pages == 0:
            return await self._record_failure(
                attachment, session_id, user_id, "pdf", "pdf has 0 pages"
            )
        if pdf_pages > self._settings.max_pdf_pages:
            raise IngestRejectedError(
                f"pdf has {pdf_pages} pages; limit is {self._settings.max_pdf_pages}",
                reason="page_count",
            )

        pdf_hash = hash_bytes(attachment.data)
        blob_id, _is_new_blob, _pdf_minio_key = await self._upsert_blob(
            user_id=user_id,
            blake3_hex=pdf_hash,
            data=attachment.data,
            content_type="application/pdf",
            page_count_=pdf_pages,
            extension="pdf",
            prefix="blobs",
        )

        file_id = int(
            await self._db.fetchval(
                """
                INSERT INTO metadata_file
                    (session_id, user_id, type, file_name, total_pages, status)
                VALUES ($1, $2, 'pdf', $3, $4, 'pending')
                RETURNING id
                """,
                (session_id, user_id, attachment.filename, pdf_pages),
            )
        )
        await self._db.link_metadata_file_blob(file_id, blob_id)

        page_outcomes: list[IngestPage] = []
        cache_hits = 0
        cache_misses = 0
        for page_num, jpg in iter_pages_as_jpg(attachment.data):
            page_hash = hash_bytes(jpg)
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
        except (OCRError, Exception) as e:
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
        if outcome.status == "failed":
            page_id = int(
                await self._db.fetchval(
                    """
                    INSERT INTO pages
                        (metadata_file_id, page, status, status_message)
                    VALUES ($1, $2, 'failed', 'OCR failed')
                    RETURNING id
                    """,
                    (file_id, outcome.page),
                )
            )
        else:
            page_id = int(
                await self._db.fetchval(
                    """
                    INSERT INTO pages
                        (metadata_file_id, page, agent_extracted, status, status_message)
                    VALUES ($1, $2, $3, 'extracted', $4)
                    RETURNING id
                    """,
                    (
                        file_id,
                        outcome.page,
                        json.dumps(outcome.extraction, ensure_ascii=False),
                        "cached" if outcome.cache_layer else "extracted",
                    ),
                )
            )
        outcome.page_id = page_id

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
        await self._db.execute(
            "UPDATE metadata_file SET status = $1, status_message = $2 WHERE id = $3",
            (outcome.status, msg, outcome.file_id),
        )

    async def _record_failure(
        self,
        attachment: FileAttachment,
        session_id: int,
        user_id: int,
        file_type: str,
        error_msg: str,
    ) -> IngestOutcome:
        """Persist a failed metadata_file row so the user/agent has visibility.

        Used for pre-flight failures (corrupt PDF, undecodable image) that
        never reach the OCR call.
        """
        file_id = int(
            await self._db.fetchval(
                """
                INSERT INTO metadata_file
                    (session_id, user_id, type, file_name, total_pages, status, status_message)
                VALUES ($1, $2, $3, $4, 0, 'failed', $5)
                RETURNING id
                """,
                (session_id, user_id, file_type, attachment.filename, error_msg),
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
        attachment: FileAttachment,
        *,
        session_id: int,
        user_id: int,
    ) -> EnqueuedFile:
        """Same as ingest() but skips inline OCR — fires a Taskiq task per
        cache-miss page. Cache hits are persisted directly (no task) so the
        orchestrator can stream them as immediate results.
        """
        from app.services.extraction.queue.tasks import extract_page_task

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
            raise IngestRejectedError(
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
                await self._persist_page(
                    file_id,
                    IngestPage(
                        page=page_num,
                        page_id=None,
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

            await extract_page_task.kiq(
                user_id=user_id,
                file_id=file_id,
                blob_id=blob_id,
                page=page_num,
            )
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
        attachment: FileAttachment,
        session_id: int,
        user_id: int,
    ) -> tuple[int, int, list[tuple[int, str]]]:
        """Image branch of enqueue — does everything except OCR + final status."""
        try:
            jpg = to_canonical_jpg(attachment.data)
        except UnsupportedImageError as e:
            file_id = await self._record_failure_row(
                attachment, session_id, user_id, "image", str(e)
            )
            raise IngestRejectedError(f"undecodable image: {e}", reason="decode")

        page_hash = hash_bytes(jpg)
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
        file_id = int(
            await self._db.fetchval(
                """
                INSERT INTO metadata_file
                    (session_id, user_id, type, file_name, total_pages, status)
                VALUES ($1, $2, 'image', $3, 1, 'pending')
                RETURNING id
                """,
                (session_id, user_id, attachment.filename),
            )
        )
        await self._db.link_metadata_file_blob(file_id, blob_id)
        return blob_id, file_id, [(1, page_hash)]

    async def _prepare_pdf(
        self,
        attachment: FileAttachment,
        session_id: int,
        user_id: int,
    ) -> tuple[int, int, list[tuple[int, str]]]:
        try:
            pdf_pages = page_count(attachment.data)
        except PDFSplitError as e:
            await self._record_failure_row(
                attachment, session_id, user_id, "pdf", str(e)
            )
            raise IngestRejectedError(f"unreadable pdf: {e}", reason="decode")
        if pdf_pages == 0:
            raise IngestRejectedError("pdf has 0 pages", reason="empty")
        if pdf_pages > self._settings.max_pdf_pages:
            raise IngestRejectedError(
                f"pdf has {pdf_pages} pages; limit is {self._settings.max_pdf_pages}",
                reason="page_count",
            )

        pdf_hash = hash_bytes(attachment.data)
        blob_id, _, _ = await self._upsert_blob(
            user_id=user_id,
            blake3_hex=pdf_hash,
            data=attachment.data,
            content_type="application/pdf",
            page_count_=pdf_pages,
            extension="pdf",
            prefix="blobs",
        )
        file_id = int(
            await self._db.fetchval(
                """
                INSERT INTO metadata_file
                    (session_id, user_id, type, file_name, total_pages, status)
                VALUES ($1, $2, 'pdf', $3, $4, 'pending')
                RETURNING id
                """,
                (session_id, user_id, attachment.filename, pdf_pages),
            )
        )
        await self._db.link_metadata_file_blob(file_id, blob_id)

        page_specs: list[tuple[int, str]] = []
        for page_num, jpg in iter_pages_as_jpg(attachment.data):
            page_hash = hash_bytes(jpg)
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
        attachment: FileAttachment,
        session_id: int,
        user_id: int,
        file_type: str,
        error_msg: str,
    ) -> int:
        return int(
            await self._db.fetchval(
                """
                INSERT INTO metadata_file
                    (session_id, user_id, type, file_name, total_pages, status, status_message)
                VALUES ($1, $2, $3, $4, 0, 'failed', $5)
                RETURNING id
                """,
                (session_id, user_id, file_type, attachment.filename, error_msg),
            )
        )
