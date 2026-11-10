# PostgreSQL ledger adapters

Shared ledger storage, catalogue reads, checked operations, approvals and
receipts for the application and the standalone MCP host. The package depends
on `ledger-core` and PostgreSQL migration libraries. It imports no application,
agent or MCP transport code.

`LedgerStore.connect_existing()` checks the installed ledger revision and opens
the process-owned pool. `LedgerStore.connect()` retains standalone ledger
initialization. Close the store when its process stops. `BorrowedConnection`
lets callers retain a task or ownership-lock connection for their operations.

The installed package owns the ledger Alembic history and the existing shared
migration helpers under `ledger_postgres.migrations`. Application composition
still selects ledger-before-application release order. The two histories,
revision IDs, version tables and frozen SQL/JSON assets retain their contracts.

```bash
python -m ledger_postgres.migrations upgrade --database-url "$DATABASE_URL"
python -m ledger_postgres.migrations check --database-url "$DATABASE_URL"
```

The `mcp-ledger` distribution retains existing `ledger.*` compatibility imports.
The adapter package itself works without that distribution or application tables.
