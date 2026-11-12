"""Shared PostgreSQL fixtures for integration tests."""

from collections.abc import AsyncIterator
import asyncio
from uuid import uuid4

import asyncpg
import pytest

from klaudia_api.adapters.documents.database import AppDBClient
from klaudia_api.composition.settings import Settings
from tests.integration.postgres import POSTGRES_TEST_URL, reset_postgres_database


@pytest.fixture(scope="session")
def migrated_postgres() -> None:
    """Apply the release histories once for tests that require the application."""
    from klaudia_api.adapters.persistence.migrations import upgrade_database

    asyncio.run(upgrade_database(POSTGRES_TEST_URL))


@pytest.fixture
async def postgres_db(migrated_postgres) -> AsyncIterator[AppDBClient]:
    """Provide a clean connection to the isolated PostgreSQL test database."""
    database = AppDBClient(Settings(_env_file=None, DATABASE_URL=POSTGRES_TEST_URL))
    try:
        await database.connect()
    except Exception:
        pytest.skip("The isolated PostgreSQL test database is unavailable")
    await reset_postgres_database(database)
    yield database
    await database.close()


@pytest.fixture
async def migration_database():
    """Create and remove one test database without changing the installed stores."""
    from urllib.parse import urlsplit, urlunsplit

    connection_parts = urlsplit(POSTGRES_TEST_URL)
    if connection_parts.hostname not in {"localhost", "127.0.0.1"}:
        pytest.fail("Migration tests require an explicit local PostgreSQL test server")
    admin = await asyncpg.connect(POSTGRES_TEST_URL)
    database_name = "klaudia_migration_" + uuid4().hex
    database_url = urlunsplit(connection_parts._replace(path="/" + database_name))
    created = False
    connection = None
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}" TEMPLATE template0')
        created = True
        connection = await asyncpg.connect(database_url)
        yield database_url, connection
    finally:
        try:
            if connection is not None:
                await connection.close()
            if created:
                await admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()",
                    database_name,
                )
                await admin.execute(f'DROP DATABASE "{database_name}"')
        finally:
            await admin.close()
