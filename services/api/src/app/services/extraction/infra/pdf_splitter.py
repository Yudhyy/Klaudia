"""Retain supported imports from the installed service."""

from klaudia_api.adapters.documents.media.pdf_splitter import (
    DEFAULT_JPEG_QUALITY as DEFAULT_JPEG_QUALITY,
)
from klaudia_api.adapters.documents.media.pdf_splitter import (
    DEFAULT_MAX_LONG_EDGE as DEFAULT_MAX_LONG_EDGE,
)
from klaudia_api.adapters.documents.media.pdf_splitter import Iterator as Iterator
from klaudia_api.adapters.documents.media.pdf_splitter import (
    PDFSplitError as PDFSplitError,
)
from klaudia_api.adapters.documents.media.pdf_splitter import RENDER_DPI as RENDER_DPI
from klaudia_api.adapters.documents.media.pdf_splitter import (
    RENDER_SCALE as RENDER_SCALE,
)
from klaudia_api.adapters.documents.media.pdf_splitter import io as io
from klaudia_api.adapters.documents.media.pdf_splitter import (
    iter_pages_as_jpg as iter_pages_as_jpg,
)
from klaudia_api.adapters.documents.media.pdf_splitter import logger as logger
from klaudia_api.adapters.documents.media.pdf_splitter import logging as logging
from klaudia_api.adapters.documents.media.pdf_splitter import page_count as page_count
from klaudia_api.adapters.documents.media.pdf_splitter import pdfium as pdfium
from klaudia_api.adapters.documents.media.pdf_splitter import (
    split_to_jpg as split_to_jpg,
)
from klaudia_api.adapters.documents.media.pdf_splitter import (
    to_canonical_jpg as to_canonical_jpg,
)
