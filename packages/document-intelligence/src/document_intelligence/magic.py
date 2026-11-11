"""Magic-byte verification for uploaded files.

Clients sometimes lie in Content-Type, or set image/jpeg for an SVG/HTML
file. We verify the first few bytes match a supported binary signature so
the OCR pipeline (and downstream DB rows that record `type`) never trust a
spoofed mime.

This is intentionally minimal — no full libmagic dep, just signatures we
care about. Returns a canonical content_type or None when unrecognized.
"""

from __future__ import annotations

# (signature_bytes, mime_type). Order matters: more specific first.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # webp uses RIFF container; refine below
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF-", "application/pdf"),
)

# HEIC/HEIF have an ftyp box at offset 4. We check the brand inside.
_HEIF_BRANDS = (
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"heim",
    b"heis",
    b"hevm",
    b"hevs",
    b"mif1",
    b"msf1",
    b"heif",
)


def detect_mime(data: bytes) -> str | None:
    if len(data) < 8:
        return None
    for sig, mime in _SIGNATURES:
        if data.startswith(sig):
            # Refine WebP: RIFF container can hold AVI too.
            if mime == "image/webp":
                if data[8:12] == b"WEBP":
                    return "image/webp"
                continue
            return mime
    # HEIC / HEIF: bytes[4:8] == b'ftyp', bytes[8:12] is brand
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "image/heif"
    return None


_ALLOWED = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "image/heif",
        "application/pdf",
    }
)


def is_allowed(mime: str | None) -> bool:
    return mime in _ALLOWED
