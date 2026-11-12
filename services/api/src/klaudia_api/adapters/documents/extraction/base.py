"""ExtractionAgent: thin facade over IngestService.

The agent existed before dedup/queue/MinIO. We keep it as the orchestrator's
entrypoint so the rest of the pipeline doesn't need to know about Redis,
MinIO, or BLAKE3 — those stay sealed inside the ingest layer. The agent's
job here is purely:
    - Run a Langfuse span around the ingest call (observability boundary)
    - Translate IngestOutcome -> ExtractionResult so the orchestrator's
      existing context-builder format stays unchanged.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
import re

from klaudia_api.exceptions import IngestRejectedError
from klaudia_api.interfaces.http.models.attachment import FileAttachment
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.documents.ingest import IngestService
from document_intelligence.ingest import IngestOutcome

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    file_id: int
    file_name: str
    pages: list[dict[str, Any]] = field(default_factory=list)
    status: str = "completed"

    @property
    def summary(self) -> str:
        total_items = 0
        grand_total = 0
        for p in self.pages:
            extraction = p.get("extraction", {})
            total_items += len(extraction.get("items", []))
            gt = extraction.get("payment", {}).get("grand_total", 0)
            if gt not in ("", None):
                digits = re.sub(r"\D", "", str(gt))  # keep only digits
                if digits:
                    grand_total += int(digits)
        return (
            f"File {self.file_name}: {len(self.pages)} page(s), "
            f"{total_items} item(s), total {grand_total:,}"
        )


class ExtractionAgent:
    """Facade — delegates to IngestService, exposes legacy ExtractionResult."""

    def __init__(
        self,
        ingest_service: IngestService,
        langfuse: LangfuseService | None = None,
    ) -> None:
        self._ingest = ingest_service
        self._langfuse = langfuse

    async def process(
        self,
        attachment: FileAttachment,
        session_id: int,
        user_id: int,
    ) -> ExtractionResult:
        logger.info("Processing attachment: %s", attachment.filename)

        span_cm = (
            self._langfuse.span(
                "extraction_agent.process",
                as_type="agent",
                input={
                    "file_name": attachment.filename,
                    "content_type": attachment.content_type,
                    "bytes": len(attachment.data),
                },
                metadata={"session_id": session_id, "user_id": user_id},
            )
            if self._langfuse is not None
            else _nullspan()
        )

        with span_cm as obs:
            try:
                outcome = await self._ingest.ingest(
                    attachment, session_id=session_id, user_id=user_id
                )
            except IngestRejectedError as e:
                logger.warning("Ingest rejected: %s", e)
                if obs is not None:
                    try:
                        obs.update(level="WARNING", status_message=str(e))
                    except Exception:
                        pass
                # Surface a synthetic ExtractionResult so orchestrator can
                # respond gracefully without dropping the user message.
                return ExtractionResult(
                    file_id=0,
                    file_name=attachment.filename,
                    pages=[],
                    status="rejected",
                )

            result = _outcome_to_result(outcome)

            if obs is not None:
                try:
                    obs.update(
                        output={
                            "file_id": result.file_id,
                            "pages": len(result.pages),
                            "status": result.status,
                            "summary": result.summary,
                            "cache_hits": outcome.cache_hits,
                            "cache_misses": outcome.cache_misses,
                        }
                    )
                except Exception:
                    pass
            return result


def _outcome_to_result(outcome: IngestOutcome) -> ExtractionResult:
    pages_payload = [
        {
            "page_id": page.page_id,
            "page": page.page,
            "extraction": page.extraction,
            "status": page.status,
            "from_cache": page.cache_layer is not None,
        }
        for page in outcome.pages
    ]
    return ExtractionResult(
        file_id=outcome.file_id,
        file_name=outcome.file_name,
        pages=pages_payload,
        status=outcome.status,
    )


@contextmanager
def _nullspan():
    yield None
