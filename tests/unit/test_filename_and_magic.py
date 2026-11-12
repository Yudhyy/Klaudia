"""Filename sanitization + magic-byte detection unit tests."""

import io

from PIL import Image

from klaudia_api.adapters.documents.media.filename import sanitize_filename
from klaudia_api.adapters.documents.media.magic import detect_mime, is_allowed


def test_sanitize_strips_path_components():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("/abs/path/photo.jpg") == "photo.jpg"
    assert sanitize_filename("C:\\Users\\foo\\bar.png") == "bar.png"


def test_sanitize_neutralizes_null_byte():
    assert "\x00" not in sanitize_filename("foo\x00.jpg")


def test_sanitize_replaces_shell_active_chars():
    out = sanitize_filename("name with <weird>chars?.jpg")
    assert "<" not in out and ">" not in out and "?" not in out


def test_sanitize_handles_reserved_windows_name():
    assert sanitize_filename("CON.jpg").startswith("_")
    assert sanitize_filename("LPT1.pdf").startswith("_")


def test_sanitize_falls_back_when_empty():
    assert sanitize_filename("") == "upload"
    assert sanitize_filename(None) == "upload"
    assert sanitize_filename("...") == "upload"


def test_sanitize_caps_length():
    long = "a" * 500 + ".jpg"
    out = sanitize_filename(long)
    assert len(out) <= 200
    assert out.endswith(".jpg")


def _jpg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_detect_mime_jpeg():
    assert detect_mime(_jpg_bytes()) == "image/jpeg"


def test_detect_mime_png():
    assert detect_mime(_png_bytes()) == "image/png"


def test_detect_mime_pdf():
    assert detect_mime(b"%PDF-1.7\n%foo\n" + b"\x00" * 32) == "application/pdf"


def test_detect_mime_rejects_unknown():
    assert detect_mime(b"<html><body>not an image</body></html>") is None


def test_detect_mime_rejects_short():
    assert detect_mime(b"\x00") is None


def test_is_allowed():
    assert is_allowed("image/jpeg")
    assert is_allowed("application/pdf")
    assert not is_allowed("text/html")
    assert not is_allowed(None)
