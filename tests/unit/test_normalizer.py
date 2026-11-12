"""Image normalizer unit tests.

Verifies the contract: any supported input format -> canonical JPG bytes
that downstream code (vLLM, hasher) can rely on.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from klaudia_api.adapters.documents.media.normalizer import (
    UnsupportedImageError,
    is_pdf,
    is_supported_image,
    to_canonical_jpg,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_IMAGE = _PROJECT_ROOT / "sample-data" / "receipt" / "001-receipt.jpeg"


JPEG_SOI = b"\xff\xd8\xff"


def _png_bytes(size=(64, 64), color=(255, 0, 0, 128), mode="RGBA"):
    buf = io.BytesIO()
    img = Image.new(mode, size, color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_to_canonical_jpg_returns_jpeg_bytes():
    out = to_canonical_jpg(_SAMPLE_IMAGE.read_bytes())
    assert out[:3] == JPEG_SOI


def test_to_canonical_jpg_deterministic_for_same_input():
    """Same input bytes -> same output bytes. This is what dedup relies on:
    two uploads of the same file produce the same hash. Re-encoding a JPG
    twice is *not* expected to be byte-identical (JPEG requantizes), but
    we never do that in production — input is always the user's original.
    """
    raw = _SAMPLE_IMAGE.read_bytes()
    assert to_canonical_jpg(raw) == to_canonical_jpg(raw)


def test_to_canonical_jpg_flattens_rgba_on_white():
    out = to_canonical_jpg(_png_bytes(mode="RGBA"))
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB"


def test_to_canonical_jpg_flattens_palette_mode():
    buf = io.BytesIO()
    Image.new("P", (32, 32), 0).save(buf, format="PNG")
    out = to_canonical_jpg(buf.getvalue())
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB"


def test_to_canonical_jpg_clamps_long_edge():
    big = Image.new("RGB", (5000, 3000), (255, 255, 255))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    out = to_canonical_jpg(buf.getvalue(), max_long_edge=2048)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 2048


def test_to_canonical_jpg_raises_on_garbage():
    with pytest.raises(UnsupportedImageError):
        to_canonical_jpg(b"not an image")


def test_is_supported_image_by_mime():
    assert is_supported_image("foo.bin", "image/png")
    assert is_supported_image(None, "image/jpeg")
    assert not is_supported_image("foo.bin", "application/pdf")


def test_is_supported_image_by_extension():
    assert is_supported_image("photo.HEIC", None)
    assert is_supported_image("doc.tiff", None)
    assert not is_supported_image("doc.txt", None)


def test_is_pdf():
    assert is_pdf("doc.pdf", None)
    assert is_pdf(None, "application/pdf")
    assert not is_pdf("photo.jpg", "image/jpeg")
