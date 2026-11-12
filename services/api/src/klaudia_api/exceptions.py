class KlaudiaError(Exception):
    """Base exception for Klaudia system."""


class OCRError(KlaudiaError):
    """OCR processing failed."""


class LLMError(KlaudiaError):
    """LLM call failed."""


class IngestRejectedError(KlaudiaError):
    """File-shape guardrail rejected the upload (size, type, page count, count).

    The orchestrator uses this type to return a file-specific response.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason
