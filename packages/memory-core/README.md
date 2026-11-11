# Memory core

Contracts and read/edit interfaces for owner-scoped context documents.
`DocumentEdit` validates content, expected revisions, and optional structured
policy. `MemoryDocument` represents an active revision, a tombstone, or a
missing document. `ReconciliationPolicy` stores explicit applicability and
exact decimal parameters.

Callers supply a `DocumentReader` for current context or a
`DocumentRepository` for edits. The repository commits each revision with its
history. `MemoryReader` binds a supplied reader to the authenticated owner and
returns context evidence with explicit trust and validation labels.

The package depends on Pydantic. The application supplies authentication,
PostgreSQL storage, and task connections. Ledger reconciliation checks policy
applicability and performs the financial calculation.
