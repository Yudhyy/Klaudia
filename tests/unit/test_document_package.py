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
        ("app.services.extraction.ingest", "ingest", "IngestOutcome"),
        ("app.services.extraction.ingest", "ingest", "EnqueuedFile"),
        ("app.services.extraction.infra.filename", "filename", "sanitize_filename"),
        ("app.services.extraction.infra.magic", "magic", "detect_mime"),
        ("app.services.extraction.infra.normalizer", "errors", "UnsupportedImageError"),
        ("app.services.extraction.infra.pdf_splitter", "errors", "PDFSplitError"),
        ("app.services.extraction.agents.config", "schema", "EXTRACTION_SCHEMA"),
        ("app.services.extraction.agents.schema", "schema", "validate_and_merge"),
    ],
)
def test_document_imports_share_one_definition(legacy, canonical, name):
    """Keep dataclasses, exceptions and pure rules identical for existing callers."""
    old_module = import_module(legacy)
    new_module = import_module(f"document_intelligence.{canonical}")
    assert getattr(old_module, name) is getattr(new_module, name)
