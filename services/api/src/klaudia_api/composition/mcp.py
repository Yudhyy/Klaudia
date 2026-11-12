"""Own the MCP process ledger store and its lifecycle."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from ledger_postgres.store import DEFAULT_WORKSPACE, LedgerStore


@asynccontextmanager
async def ledger_lifespan(server: FastMCP) -> AsyncIterator[LedgerStore]:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("mcp-ledger requires DATABASE_URL (Postgres DSN)")
    store = LedgerStore(dsn)
    schema_mode = os.environ.get("LEDGER_SCHEMA_MODE", "initialize")
    if schema_mode == "initialize":
        await store.connect()
    elif schema_mode == "check":
        await store.connect_existing()
    else:
        raise RuntimeError("LEDGER_SCHEMA_MODE must be initialize or check")
    # The default workspace's key comes from env, not create_spreadsheet, so
    # provision its spreadsheet row here or sheet writes would violate the FK.
    await store.ensure_spreadsheet(DEFAULT_WORKSPACE)
    try:
        yield store
    finally:
        await store.close()
