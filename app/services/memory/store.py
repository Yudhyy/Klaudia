"""Compatibility imports for application memory persistence."""

from app.adapters.persistence.memory import (
    PostgresMemoryDocumentStore as MemoryDocumentStore,
)
from memory_core.ports import DocumentConflict

__all__ = ["DocumentConflict", "MemoryDocumentStore"]
