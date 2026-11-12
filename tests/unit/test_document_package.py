"""Retain document compatibility imports through the installed package."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("legacy", "canonical", "name"),
    [
        ("app.services.documents.contracts", "contracts", "ExtractionScope"),
        ("app.services.documents.contracts", "contracts", "PageOutcome"),
        ("app.services.documents.contracts", "contracts", "FileOutcome"),
        ("app.services.documents.ports", "ports", "DocumentArchive"),
        ("app.services.documents.ports", "ports", "DocumentIngestionStore"),
        ("app.services.documents.ports", "ports", "ExtractionOutcomeStore"),
        ("klaudia_api.adapters.documents.ingest", "ingest", "IngestOutcome"),
        ("klaudia_api.adapters.documents.ingest", "ingest", "EnqueuedFile"),
        (
            "klaudia_api.adapters.documents.media.filename",
            "filename",
            "sanitize_filename",
        ),
        ("klaudia_api.adapters.documents.media.magic", "magic", "detect_mime"),
        (
            "klaudia_api.adapters.documents.media.normalizer",
            "errors",
            "UnsupportedImageError",
        ),
        (
            "klaudia_api.adapters.documents.media.pdf_splitter",
            "errors",
            "PDFSplitError",
        ),
        (
            "klaudia_api.adapters.documents.extraction.config",
            "schema",
            "EXTRACTION_SCHEMA",
        ),
        (
            "klaudia_api.adapters.documents.extraction.schema",
            "schema",
            "validate_and_merge",
        ),
    ],
)
def test_document_imports_share_one_definition(legacy, canonical, name):
    """Keep dataclasses, exceptions and pure rules identical for existing callers."""
    old_module = import_module(legacy)
    new_module = import_module(f"document_intelligence.{canonical}")
    assert getattr(old_module, name) is getattr(new_module, name)
