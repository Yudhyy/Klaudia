"""Revision and scope guarantees for human-authored context documents."""

import asyncio

import asyncpg
import pytest
from pydantic import ValidationError

from app.services.memory.contracts import DocumentEdit, DocumentPath
from app.services.memory.store import DocumentConflict, MemoryDocumentStore


@pytest.fixture
async def documents(postgres_db):
    """Provide a document store using the isolated application database."""
    store = MemoryDocumentStore(postgres_db.pool)
    await store.initialize()
    return store


async def test_revision_delete_and_restore(documents):
    """Retain revisions through deletion and reject stale restorations."""
    path = DocumentPath.PREFERENCES
    missing = await documents.read(1, path)
    assert (missing.status, missing.revision, missing.content) == ("missing", 0, None)
    created = await documents.write(
        1,
        path,
        DocumentEdit(expected_revision=0, content="Use USD", source_note="User choice"),
    )
    assert (created.revision, created.content, created.status) == (
        1,
        "Use USD",
        "active",
    )
    assert created.actor_id == 1
    assert created.source_note == "User choice"
    assert created.updated_at is not None
    deleted = await documents.delete(1, path, 1)
    assert (deleted.revision, deleted.status, deleted.content) == (2, "deleted", None)
    with pytest.raises(DocumentConflict):
        await documents.write(
            1, path, DocumentEdit(expected_revision=0, content="Stale")
        )
    restored = await documents.write(
        1, path, DocumentEdit(expected_revision=2, content="Use EUR")
    )
    assert restored.revision == 3
    assert restored.content == "Use EUR"


async def test_concurrent_creates_have_one_winner(documents):
    """Allow exactly one writer to create the same scoped document."""
    outcomes = await asyncio.gather(
        *(
            documents.write(
                1,
                DocumentPath.CONVENTIONS,
                DocumentEdit(expected_revision=0, content=text),
            )
            for text in ("First", "Second")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, DocumentConflict) for outcome in outcomes) == 1
    current = await documents.read(1, DocumentPath.CONVENTIONS)
    assert current.revision == 1
    assert current.content in {"First", "Second"}


async def test_owner_scope_and_revision_history(documents, postgres_db):
    """Keep another owner's reads and writes separate and retain exact revisions."""
    await postgres_db.execute(
        'INSERT INTO "user" (user_id, username, email, password_hash) VALUES (2, $1, $2, $3)',
        ("second", "second@example.test", "fixture"),
    )
    path = DocumentPath.PREFERENCES
    await documents.write(1, path, DocumentEdit(expected_revision=0, content="Private"))
    assert (await documents.read(2, path)).status == "missing"
    with pytest.raises(DocumentConflict):
        await documents.delete(2, path, 1)
    await documents.write(2, path, DocumentEdit(expected_revision=0, content="Other"))
    await documents.delete(1, path, 1)
    assert (await documents.read(2, path)).content == "Other"
    rows = await postgres_db.fetchall(
        "SELECT revision, content, status FROM memory_document_revision WHERE user_id = 1 ORDER BY revision"
    )
    assert [(row["revision"], row["content"], row["status"]) for row in rows] == [
        (1, "Private", "active"),
        (2, None, "deleted"),
    ]


@pytest.mark.parametrize("content", ["x" * 8193, "€" * 2731])
def test_document_limit_counts_utf8_bytes(content):
    """Reject content beyond the byte limit, including multibyte text."""
    with pytest.raises(ValidationError):
        DocumentEdit(expected_revision=0, content=content)


def test_document_rejects_forged_provenance():
    """Do not accept client-supplied actor identity or trusted timestamps."""
    with pytest.raises(ValidationError):
        DocumentEdit(expected_revision=0, content="Text", actor_id=99)


def test_source_note_rejects_null_bytes():
    """Reject source notes that PostgreSQL cannot store as text."""
    with pytest.raises(ValidationError):
        DocumentEdit(expected_revision=0, content="Text", source_note="\x00")


async def test_history_failure_rolls_back_current_document(documents, postgres_db):
    """Keep the current value unchanged when its history row cannot commit."""
    path = DocumentPath.CONVENTIONS
    await documents.write(
        1, path, DocumentEdit(expected_revision=0, content="Original")
    )
    async with postgres_db.pool.acquire() as connection:
        await connection.execute(
            "ALTER TABLE memory_document_revision ADD CONSTRAINT test_revision_limit CHECK (revision < 2)"
        )
    try:
        with pytest.raises(asyncpg.CheckViolationError, match="test_revision_limit"):
            await documents.write(
                1, path, DocumentEdit(expected_revision=1, content="Changed")
            )
        current = await documents.read(1, path)
        assert (current.revision, current.content) == (1, "Original")
    finally:
        await postgres_db.execute(
            "ALTER TABLE memory_document_revision DROP CONSTRAINT test_revision_limit"
        )


async def test_concurrent_updates_have_one_winner(documents, postgres_db):
    """Commit exactly one replacement of a shared observed revision."""
    path = DocumentPath.PREFERENCES
    await documents.write(
        1, path, DocumentEdit(expected_revision=0, content="Original")
    )
    outcomes = await asyncio.gather(
        *(
            documents.write(1, path, DocumentEdit(expected_revision=1, content=text))
            for text in ("First", "Second")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, DocumentConflict) for outcome in outcomes) == 1
    current = await documents.read(1, path)
    assert current.revision == 2
    assert current.content in {"First", "Second"}
    assert (
        await postgres_db.fetchval("SELECT count(*) FROM memory_document_revision") == 2
    )


def test_document_accepts_exact_utf8_budget():
    """Keep exact text at the maximum supported byte count."""
    content = "€" * 2730 + "ab"
    assert DocumentEdit(expected_revision=0, content=content).content == content
