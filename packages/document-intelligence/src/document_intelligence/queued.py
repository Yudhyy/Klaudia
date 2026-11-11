"""Process one owned queued page with supplied extraction capabilities."""

import json
import logging
from typing import Any

from document_intelligence.contracts import ExtractionScope, PageOutcome, QueuedPage
from document_intelligence.ports import ExtractionServices, PublishProgress
from document_intelligence.schema import validate_and_merge

logger = logging.getLogger(__name__)


class QueuedExtraction:
    """Save page results before notifying progress listeners."""

    def __init__(self, services: ExtractionServices, publish: PublishProgress) -> None:
        """Bind page processing to the worker's supplied stores and publisher."""
        self._services = services
        self._publish = publish

    async def run(self, job: QueuedPage) -> dict[str, Any]:
        """Resolve an owned page, extract or reuse its facts, and save its outcome."""
        db = self._services.documents
        ocr = self._services.ocr
        scope, row = await self._services.outcomes.resolve_page(job)
        if row["page_blake3"] is None:
            return await self._fail(
                scope, job.page, "Prepared page reference is missing"
            )

        page_hash = row["page_blake3"]
        page_key = row["page_minio_key"]
        cached = await self._cached(job.user_id, page_hash)
        if cached is not None:
            await self._save(scope, PageOutcome(job.page, "cached", cached, "redis"))
            return {"status": "cached", "cache_layer": "redis"}

        database_row = await db.get_cached_extraction(job.user_id, page_hash)
        if database_row is not None:
            try:
                extraction = json.loads(database_row["extraction_json"])
                await self._cache(job.user_id, page_hash, extraction)
                await self._save(
                    scope, PageOutcome(job.page, "cached", extraction, "database")
                )
                return {"status": "cached", "cache_layer": "database"}
            except json.JSONDecodeError:
                logger.warning("blob_extraction row corrupted; re-extracting")

        try:
            jpg = await self._services.store.get(page_key)
        except Exception as error:
            message = f"object_store.get({page_key!r}) failed: {error}"
            logger.error(message)
            return await self._fail(scope, job.page, message)

        try:
            raw = await ocr.extract_from_image(jpg)
            validated = validate_and_merge(raw)
        except Exception as error:
            return await self._fail(scope, job.page, str(error))

        await db.upsert_extraction(
            user_id=job.user_id,
            page_blake3=page_hash,
            extraction_json=json.dumps(validated, ensure_ascii=False),
            ocr_model=ocr.model_id,
            schema_version=ocr.schema_version,
        )
        await self._cache(job.user_id, page_hash, validated)
        await self._save(scope, PageOutcome(job.page, "extracted", validated))
        return {"status": "extracted"}

    async def _save(self, scope: ExtractionScope, page: PageOutcome) -> None:
        """Commit the outcome before sending a progress notification."""
        await self._services.outcomes.save(scope, page)
        await self._publish(scope.file_id, page)

    async def _fail(
        self, scope: ExtractionScope, page: int, message: str
    ) -> dict[str, Any]:
        """Save a terminal failure with the existing worker result shape."""
        await self._save(scope, PageOutcome(page, "failed", error=message))
        return {"status": "failed", "error": message}

    async def _cached(self, user_id: int, page_hash: str) -> dict[str, Any] | None:
        """Read the optional cache and allow a database fallback on failure."""
        try:
            return await self._services.cache.get_extraction(user_id, page_hash)
        except Exception as error:
            logger.warning("cache get failed: %s", error)
            return None

    async def _cache(
        self, user_id: int, page_hash: str, extraction: dict[str, Any]
    ) -> None:
        """Warm the optional cache without losing a saved extraction."""
        try:
            await self._services.cache.set_extraction(user_id, page_hash, extraction)
        except Exception as error:
            logger.warning("cache set failed: %s", error)
