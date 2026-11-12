"""Retain supported imports from the installed service."""

from klaudia_api.adapters.documents.media.normalizer import (
    DEFAULT_JPEG_QUALITY as DEFAULT_JPEG_QUALITY,
)
from klaudia_api.adapters.documents.media.normalizer import (
    DEFAULT_MAX_LONG_EDGE as DEFAULT_MAX_LONG_EDGE,
)
from klaudia_api.adapters.documents.media.normalizer import Image as Image
from klaudia_api.adapters.documents.media.normalizer import ImageOps as ImageOps
from klaudia_api.adapters.documents.media.normalizer import (
    SUPPORTED_IMAGE_EXTENSIONS as SUPPORTED_IMAGE_EXTENSIONS,
)
from klaudia_api.adapters.documents.media.normalizer import (
    SUPPORTED_IMAGE_MIME_TYPES as SUPPORTED_IMAGE_MIME_TYPES,
)
from klaudia_api.adapters.documents.media.normalizer import (
    UnsupportedImageError as UnsupportedImageError,
)
from klaudia_api.adapters.documents.media.normalizer import io as io
from klaudia_api.adapters.documents.media.normalizer import is_pdf as is_pdf
from klaudia_api.adapters.documents.media.normalizer import (
    is_supported_image as is_supported_image,
)
from klaudia_api.adapters.documents.media.normalizer import logger as logger
from klaudia_api.adapters.documents.media.normalizer import logging as logging
from klaudia_api.adapters.documents.media.normalizer import (
    to_canonical_jpg as to_canonical_jpg,
)
