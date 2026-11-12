"""PDF splitter unit tests."""

from pathlib import Path

import pytest

from klaudia_api.adapters.documents.media.pdf_splitter import (
    PDFSplitError,
    iter_pages_as_jpg,
    page_count,
    split_to_jpg,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_PDF = _PROJECT_ROOT / "sample-data" / "pdf" / "001-receipt.pdf"


JPEG_SOI = b"\xff\xd8\xff"


def test_page_count_matches_fixture():
    assert page_count(_SAMPLE_PDF.read_bytes()) == 6


def test_iter_pages_yields_jpg_per_page():
    pdf = _SAMPLE_PDF.read_bytes()
    pages = list(iter_pages_as_jpg(pdf))
    assert len(pages) == 6
    for page_num, jpg in pages:
        assert isinstance(page_num, int)
        assert 1 <= page_num <= 6
        assert jpg[:3] == JPEG_SOI
        assert len(jpg) > 1024  # sanity: not empty / not header-only


def test_iter_pages_supports_subset():
    pdf = _SAMPLE_PDF.read_bytes()
    pages = list(iter_pages_as_jpg(pdf, pages=[1, 3, 6]))
    nums = [p for p, _ in pages]
    assert nums == [1, 3, 6]


def test_iter_pages_rejects_invalid_subset():
    pdf = _SAMPLE_PDF.read_bytes()
    with pytest.raises(PDFSplitError):
        list(iter_pages_as_jpg(pdf, pages=[99]))


def test_split_to_jpg_materializes_list():
    pdf = _SAMPLE_PDF.read_bytes()
    jpgs = split_to_jpg(pdf)
    assert len(jpgs) == 6
    assert all(j[:3] == JPEG_SOI for j in jpgs)


def test_split_raises_on_garbage_bytes():
    with pytest.raises(PDFSplitError):
        page_count(b"not a pdf")
