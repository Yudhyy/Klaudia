"""Failures owned by document ingestion and media preparation."""


class UploadRejectedError(ValueError):
    """Reject an upload with the current machine-readable reason."""

    def __init__(self, message: str, *, reason: str) -> None:
        """Keep the rejection message and reason for the calling adapter."""
        super().__init__(message)
        self.reason = reason


class ExtractionSessionNotFoundError(LookupError):
    """The requested session is unavailable to the extraction owner."""


class UnsupportedImageError(ValueError):
    """The image could not be decoded or normalized."""


class PDFSplitError(ValueError):
    """The PDF could not be opened or rendered as requested."""
