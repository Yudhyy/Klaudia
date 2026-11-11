"""Classify supported document media using verified MIME and file names."""

from pathlib import PurePath
from typing import Final

SUPPORTED_IMAGE_EXTENSIONS: Final = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
    ".tiff",
    ".tif",
    ".bmp",
)

SUPPORTED_IMAGE_MIME_TYPES: Final = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/tiff",
    "image/bmp",
)


def is_supported_image(filename: str | None, content_type: str | None) -> bool:
    if content_type and content_type.lower() in SUPPORTED_IMAGE_MIME_TYPES:
        return True
    if filename:
        ext = PurePath(filename).suffix.lower()
        return ext in SUPPORTED_IMAGE_EXTENSIONS
    return False


def is_pdf(filename: str | None, content_type: str | None) -> bool:
    if content_type and content_type.lower() == "application/pdf":
        return True
    if filename and PurePath(filename).suffix.lower() == ".pdf":
        return True
    return False
