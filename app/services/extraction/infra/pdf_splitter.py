"""PDF -> per-page canonical JPG bytes.

Qwen3.5-4B consumes single images; PDFs are rendered page-by-page at 200 DPI
via pypdfium2 (already a project dep). Each page goes through the same
normalizer used for image uploads so dedup is consistent across formats —
i.e. if the same page is uploaded as a standalone JPG and as a PDF page,
the hash matches and extraction is reused.
"""

from __future__ import annotations

import io
import logging
from typing import Iterator

import pypdfium2 as pdfium

from document_intelligence.errors import PDFSplitError as PDFSplitError

from app.services.extraction.infra.normalizer import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_LONG_EDGE,
    to_canonical_jpg,
)

logger = logging.getLogger(__name__)

# 200 DPI is the Qwen3.5-4B default for receipt-scale text.
RENDER_DPI = 200
RENDER_SCALE = RENDER_DPI / 72


def page_count(pdf_bytes: bytes) -> int:
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as e:
        raise PDFSplitError(f"Failed to open PDF: {e}") from e
    return len(pdf)


def iter_pages_as_jpg(
    pdf_bytes: bytes,
    *,
    max_long_edge: int = DEFAULT_MAX_LONG_EDGE,
    quality: int = DEFAULT_JPEG_QUALITY,
    pages: list[int] | None = None,
) -> Iterator[tuple[int, bytes]]:
    """Yield (page_number_1based, jpg_bytes) for each PDF page.

    `pages` (1-based) lets callers extract a subset, e.g. for replay.
    Default extracts every page — users explicitly chose "always extract all"
    so multi-turn convos can reference any page later without re-upload.
    """
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as e:
        raise PDFSplitError(f"Failed to open PDF: {e}") from e

    total = len(pdf)
    if pages is None:
        target = list(range(1, total + 1))
    else:
        target = sorted({p for p in pages if 1 <= p <= total})
        if not target:
            raise PDFSplitError(
                f"No valid pages requested (pdf has {total}, asked: {pages})"
            )

    for page_num in target:
        page = pdf[page_num - 1]
        try:
            pil_image = page.render(scale=RENDER_SCALE).to_pil()
            # Render output is RGB already; pipe through normalizer for the
            # long-edge clamp + canonical JPEG encoder so hashes match the
            # standalone-image path.
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")  # lossless intermediate
            jpg = to_canonical_jpg(
                buf.getvalue(),
                max_long_edge=max_long_edge,
                quality=quality,
            )
            yield page_num, jpg
        finally:
            page.close()
    pdf.close()


def split_to_jpg(
    pdf_bytes: bytes,
    *,
    max_long_edge: int = DEFAULT_MAX_LONG_EDGE,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> list[bytes]:
    """Convenience wrapper that materializes the iterator. Use for small PDFs
    or tests; prefer iter_pages_as_jpg for memory-tight worker code paths.
    """
    return [
        jpg
        for _, jpg in iter_pages_as_jpg(
            pdf_bytes, max_long_edge=max_long_edge, quality=quality
        )
    ]
