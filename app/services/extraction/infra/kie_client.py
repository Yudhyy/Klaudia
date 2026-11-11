"""Select and run the configured receipt extraction backend."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from app.services.core.observability import LangfuseService
from document_intelligence.schema import EXTRACTION_SCHEMA
from app.services.extraction.infra.deepseek_kie import DeepSeekKIEClient
from app.services.extraction.infra.gemini_kie import GeminiKIEClient
from app.services.extraction.infra.hasher import hash_bytes
from app.services.extraction.infra.normalizer import to_canonical_jpg
from app.services.extraction.infra.pdf_splitter import iter_pages_as_jpg
from app.services.extraction.infra.vllm_kie import VLLMKIEClient
from config.settings import Settings

logger = logging.getLogger(__name__)

_FALLBACK_MOCK: dict[str, Any] = copy.deepcopy(EXTRACTION_SCHEMA)
_FALLBACK_MOCK["info"]["store_name"] = "MOCK_FALLBACK"


def _is_gemini_model(model: str) -> bool:
    return model.strip().lower().startswith("gemini")


def _is_deepseek_model(model: str) -> bool:
    return model.strip().lower().startswith("deepseek")


class KIEClient:
    """Route receipt extraction by model name."""

    def __init__(
        self,
        settings: Settings,
        langfuse: LangfuseService | None = None,
    ) -> None:
        self._mock_kie = settings.mock_kie
        self._kie_model = settings.kie_model
        self._schema_version = settings.ocr_schema_version
        self._settings = settings
        self._langfuse = langfuse

        self._deepseek: DeepSeekKIEClient | None = None
        self._gemini: GeminiKIEClient | None = None
        self._vllm: VLLMKIEClient | None = None

        self._mock_lookup: dict[str, dict[str, Any]] = {}
        if self._mock_kie:
            self._mock_lookup = _build_mock_lookup()
            logger.info(
                "KIE mock mode: %d sample fixtures indexed by content hash",
                len(self._mock_lookup),
            )

    @property
    def mode(self) -> str:
        if self._mock_kie:
            return "mock"
        if _is_deepseek_model(self._kie_model):
            return "deepseek"
        if _is_gemini_model(self._kie_model):
            return "gemini"
        return "vllm"

    @property
    def model_id(self) -> str:
        """Return the model identifier stored with extraction output."""
        if self._mock_kie:
            return f"{self._kie_model}-mock"
        return self._kie_model

    @property
    def schema_version(self) -> str:
        return self._schema_version

    async def extract_from_image(self, jpg_bytes: bytes) -> dict[str, Any]:
        if self._mock_kie:
            return self._mock_extract(jpg_bytes)
        if _is_deepseek_model(self._kie_model):
            return await self._deepseek_extract(jpg_bytes)
        if _is_gemini_model(self._kie_model):
            return await self._gemini_extract(jpg_bytes)
        return await self._vllm_extract(jpg_bytes)

    def _mock_extract(self, jpg_bytes: bytes) -> dict[str, Any]:
        h = hash_bytes(jpg_bytes)
        result = self._mock_lookup.get(h)
        if result is None:
            logger.warning(
                "Mock KIE: no fixture for hash %s; returning fallback", h[:12]
            )
            return copy.deepcopy(_FALLBACK_MOCK)
        return copy.deepcopy(result)

    async def _gemini_extract(self, jpg_bytes: bytes) -> dict[str, Any]:
        if self._gemini is None:
            self._gemini = GeminiKIEClient(self._settings, self._langfuse)
        return await self._gemini.extract_from_image(jpg_bytes)

    async def _deepseek_extract(self, jpg_bytes: bytes) -> dict[str, Any]:
        if self._deepseek is None:
            self._deepseek = DeepSeekKIEClient(self._settings, self._langfuse)
        return await self._deepseek.extract_from_image(jpg_bytes)

    async def _vllm_extract(self, jpg_bytes: bytes) -> dict[str, Any]:
        if self._vllm is None:
            self._vllm = VLLMKIEClient(self._settings, self._langfuse)
        return await self._vllm.extract_from_image(jpg_bytes)

    async def process_file(
        self, data: bytes, content_type: str
    ) -> list[dict[str, Any]]:
        """Normalize a legacy file request and extract each page."""
        if content_type == "application/pdf":
            return [
                await self.extract_from_image(jpg)
                for _page, jpg in iter_pages_as_jpg(data)
            ]
        return [await self.extract_from_image(to_canonical_jpg(data))]

    async def shutdown(self) -> None:
        if self._deepseek is not None:
            await self._deepseek.shutdown()
        if self._vllm is not None:
            await self._vllm.shutdown()


_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _build_mock_lookup() -> dict[str, dict[str, Any]]:
    """Build the mock label lookup from sample data."""
    lookup: dict[str, dict[str, Any]] = {}

    images_dir = _PROJECT_ROOT / "sample-data" / "receipt"
    image_labels_dir = _PROJECT_ROOT / "sample-data" / "labels" / "images"
    if images_dir.is_dir() and image_labels_dir.is_dir():
        image_paths = sorted(
            p
            for pattern in ("*.jpg", "*.jpeg", "*.png")
            for p in images_dir.glob(pattern)
        )
        for img_path in image_paths:
            label_path = image_labels_dir / f"{img_path.stem}.json"
            if not label_path.is_file():
                continue
            try:
                jpg = to_canonical_jpg(img_path.read_bytes())
                label = json.loads(label_path.read_text(encoding="utf-8"))
                lookup[hash_bytes(jpg)] = label
            except Exception as e:  # pragma: no cover
                logger.warning("Mock fixture skipped %s: %s", img_path, e)

    pdf_dir = _PROJECT_ROOT / "sample-data" / "pdf"
    pdf_labels_dir = _PROJECT_ROOT / "sample-data" / "labels" / "pdf"
    if pdf_dir.is_dir() and pdf_labels_dir.is_dir():
        for pdf_path in sorted(pdf_dir.glob("*.pdf")):
            label_subdir = pdf_labels_dir / pdf_path.stem
            if not label_subdir.is_dir():
                continue
            try:
                pdf_bytes = pdf_path.read_bytes()
                for page_num, jpg in iter_pages_as_jpg(pdf_bytes):
                    label_file = label_subdir / f"{page_num}.json"
                    if not label_file.is_file():
                        continue
                    label = json.loads(label_file.read_text(encoding="utf-8"))
                    lookup[hash_bytes(jpg)] = label
            except Exception as e:  # pragma: no cover
                logger.warning("Mock PDF fixture skipped %s: %s", pdf_path, e)

    return lookup
