"""Filename sanitization for user-provided uploads.

Defends against:
    - Path traversal (../, absolute paths, backslashes)
    - NUL bytes (POSIX truncation tricks)
    - Reserved Windows names (CON, AUX, ...) when files are later exfiltrated
    - Extremely long names

The sanitized filename is used ONLY for display + storage in
metadata_file.file_name. The actual storage key uses BLAKE3 — there is no
filesystem path derived from user input. This is still belt-and-suspenders
because the filename ends up in logs, SSE events, and the agent context.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

# Windows reserved device names (case-insensitive). Even if we serve on Linux,
# clients on Windows may try to download with these names.
_WIN_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)

_MAX_NAME_LEN = 200  # plenty for receipts; below FS limits on every platform
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def sanitize_filename(raw: str | None, *, fallback: str = "upload") -> str:
    """Return a safe display filename. Always non-empty."""
    if not raw:
        return fallback

    # Normalize unicode so confusables (e.g. visually-identical homoglyphs)
    # don't bypass the deny list.
    name = unicodedata.normalize("NFC", raw)

    # Strip any directory component a client might have included.
    # Normalize backslashes first so a Windows path like "C:\foo\bar.jpg"
    # reduces to "bar.jpg" on a POSIX server too.
    name = name.replace("\\", "/")
    name = PurePosixPath(name).name or name

    # Drop control chars and shell-active characters.
    name = _UNSAFE_CHARS.sub("_", name)

    # Collapse whitespace runs to single underscore (avoids spaces in URLs).
    name = re.sub(r"\s+", "_", name).strip("._")

    if not name:
        return fallback

    # Reserved Windows base names — append underscore to neutralize.
    stem = name.split(".", 1)[0].lower()
    if stem in _WIN_RESERVED:
        name = "_" + name

    if len(name) > _MAX_NAME_LEN:
        # Preserve extension when truncating
        if "." in name:
            stem_part, _, ext = name.rpartition(".")
            keep = _MAX_NAME_LEN - len(ext) - 1
            name = f"{stem_part[: max(keep, 1)]}.{ext}"
        else:
            name = name[:_MAX_NAME_LEN]

    return name
