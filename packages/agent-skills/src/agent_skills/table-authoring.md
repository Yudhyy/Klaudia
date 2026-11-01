Procedure: table-authoring (v1)

Use these procedures only when table-authoring tools are enabled.

1. Resolve the business destination first. Use search_resources for existing
   tables. Use list_authoring_sheets only to find a destination for a new table.
   Ask about the business entity or period when the destination is ambiguous.
2. Inspect the table schema and/or inspect_sheet_region. Use the observed sheet
   and catalogue revisions in the proposal. Never invent a revision or reuse stale
   metadata. Keep all unrelated regions intact.
3. Select the precise action:
   - create_table writes supplied headers into a wholly blank finite region and
     registers it. The header count must match the range width.
   - register_table registers existing headers and records without changing cells.
   - update_table replaces descriptive metadata and/or bounds on the same sheet.
     It does not move cells or rename stored headers. If the actual headers changed,
     provide one column_ids entry per new column: its known old ID, or null for a
     new identity. Never guess whether two columns represent the same business field.
   - refresh_table recomputes metadata from the current region while preserving
     unchanged column identities. Changed headers require an explicit update mapping.
   - unregister_table removes the catalogue entry and preserves all cells. It always
     needs human approval. Do not describe it as deleting financial records.
4. Preparation is not execution or approval. Execute only the returned operation
   reference. Retry that same reference after an uncertain result. The server may
   pause for approval; never try to approve your own proposal.
5. Report only changes supported by the committed receipt. Reinspect affected
   tables before later calculations or appends. Formula evaluation and physical
   row/column deletion are not supported by these tools.
