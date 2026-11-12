"""PostgreSQL integration-test configuration and reset helpers."""

import os

from klaudia_api.adapters.documents.database import AppDBClient

POSTGRES_TEST_URL = os.environ.get(
    "PG_TEST_URL",
    "postgresql://klaudia:klaudia@localhost:5433/klaudia_sandbox",
)

_APP_TABLES = (
    "metadata_file_blob",
    "blob_extraction",
    "file_blob_page",
    "file_blob",
    "pages",
    "metadata_file",
    "conversation",
    "session",
    '"user"',
)


async def reset_postgres_database(db: AppDBClient) -> None:
    """Clear application tables and restore the development seed user."""
    await db.execute("DROP TABLE IF EXISTS pending_approval")
    await db.execute(f"TRUNCATE {', '.join(_APP_TABLES)} RESTART IDENTITY CASCADE")
    await db.execute(
        """INSERT INTO "user" (user_id, username, email, password_hash)
           VALUES (1, 'dev', 'dev@local', 'not-a-real-hash')"""
    )
    await db.reset_user_id_sequence()
