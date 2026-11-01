Procedure:
1. Inspect the requested table by stable ID; search by intent first if its identity is unknown.
2. Read column pages until the relevant schema is visible. Distinguish column_count from the current page length.
3. Treat grain, description, entity and period as declared metadata. Headers come from the registered source revision.
4. Check freshness before describing the schema as current. A sheet change can make registered bounds and headers stale.
5. Column names alone do not prove financial meaning, currency, formula ownership or relationships. State unknowns and seek evidence.
6. References and revisions describe observations, not permission to write or proof that an operation occurred.
