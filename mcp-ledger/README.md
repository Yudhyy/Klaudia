# mcp-ledger

Postgres-backed MCP reads with current workbook ownership checks. The default
surface exposes nine read tools. All fourteen mutation handlers are denied,
including legacy checked append, catalogue writes, and destructive sheet tools.
Application checked proposal/execute tools and approvals remain the write path.
Existing read names and response shapes remain supported within an owned workbook.

## Configuration

| Env | Meaning |
|-----|---------|
| `DATABASE_URL` | Postgres DSN (required) |
| `LEDGER_WORKSPACE` | Default workspace when `spreadsheet_id` is omitted (default `default`) |
| `LEDGER_TITLE` | Workspace display title (default `Klaudia Ledger`) |
| `FASTMCP_HOST` / `FASTMCP_PORT` | HTTP or legacy SSE bind (default `0.0.0.0:8003`) |
| `MCP_JWT_SECRET` | HS256 verification key for HTTP bearer auth (32+ characters) |
| `MCP_JWT_ISSUER` / `MCP_JWT_AUDIENCE` | Optional JWT claim checks |
| `MCP_ALLOW_INSECURE_HTTP` | Local-only override for the network bearer gate; does not grant workbook access |
| `JWT_SECRET` | Application HS256 key to verify forwarded owner tokens (32+ characters) |
| `MCP_USER_ID` | Explicit local stdio owner; never used as HTTP/SSE authority |

## Run

```bash
python main.py --transport stdio   # local subprocess (default)
python main.py --transport http    # stateless service at /mcp; auth required
python main.py --transport sse     # legacy rollback only
```

FastMCP is pinned to `4.0.0b3`. HTTP starts in stateless mode so requests can
reach any replica. Put TLS and rate limits at the gateway. For an external
endpoint, replace the shared HS256 verifier with an asymmetric key and JWKS.

## Storage

One row per sheet tab (`ledger_sheet`), the cell grid as a single JSONB 2D
array. Grid mutations use `FOR UPDATE` row locking. A database trigger increments
the sheet revision on every update, including direct SQL edits. Snapshot reads
still load the full grid from storage before returning the requested range.

## Identity and supported reads

HTTP/SSE callers use bearer authentication. A numeric positive JWT `sub` and an
expiry identify an owner. A service credential with a nonnumeric subject grants
no workbook access by itself. Klaudia forwards its verified application token in
MCP request metadata under `authorization`; the server verifies it using
`JWT_SECRET`. A remote MCP server must use the same application verification key.
Invalid forwarded tokens fail closed rather than falling back to another identity.

For standalone stdio, configure an explicit `MCP_USER_ID`, or send a signed
application token in request metadata. An omitted identity cannot read records.
Local owner zero can inspect system-owned legacy workbooks only when explicitly
configured. The application subprocess clears this local override and supplies
each authenticated user's token separately.

Every read checks current `ledger_spreadsheet.user_id` ownership. Batch reads
check every nested workbook, including the default. A supplied workbook or table
ID does not grant permission. Ownership stays locked through the read, using the
same request-owned connection. No shared workspace ACL is introduced.

Supported tools:

- `tool_get_sheet_data`, `tool_get_sheet_formulas`
- `tool_list_sheets`, `tool_get_spreadsheet_info`
- `tool_get_multiple_sheet_data`
- `tool_get_sheet_snapshot`, `tool_aggregate_sheet`
- `tool_search_resources`, `tool_inspect_resource`

Discovery omits all mutations. Calling a mutation by name also fails before its
handler runs. `tool_append_rows_checked` is disabled: revision and retry checks
alone do not provide the application's proposal and approval contract. Use the
existing application tools for supported financial changes. No unrestricted
administrative MCP profile is included.

`tool_get_sheet_snapshot(sheet, range)` returns stable sheet identity, revision,
and literal values. Its default range is `A1:Z20`; explicit ranges must be finite
rectangles of at most 2,000 cells. Snapshot and aggregate responses retain their
65,536-byte JSON budget. Oversized responses fail rather than truncate.

## Labelled aggregation

`tool_aggregate_sheet(sheet, query)` computes `sum` and nonblank `count` metrics
over a finite `table_range`. Its first row supplies unique headers. Select the
table itself, excluding nearby tables, subtotals and summary footers. Queries
support exact equality filters and up to three grouping columns. Numeric grouping
treats `1` and `1.0` alike while keeping booleans and text distinct.

The result includes the stable source sheet ID, revision, range, applied query,
matched record count and groups. Each metric retains its column and operation,
with its value encoded as decimal text and explicit nonblank/blank counts.
Empty cells are excluded; invalid selected operands abort the query. Raw records
are not returned. Limits are one million selected cells, eight metrics and up to
100 groups (20 by default). Group overflow fails instead of returning partial totals.

Sums use a fixed 64-digit decimal context and reject inexact arithmetic. Raw
numbers are accepted by default. For text known to use a decimal point without
thousands separators, set `numeric_text: "decimal"`. This is an explicit source
format declaration; the tool does not guess whether `15.000` means fifteen or
fifteen thousand. Currency symbols, comma separators and formulas are rejected.

Declare `unit_column` for currency/unit checks. Every matched row must then have
a nonblank text unit, and units must agree within each group. Filter or group by
currency to keep currencies separate. Without this declaration, unit is
unspecified; this tool makes no accounting-policy or currency-correctness claim.
Catalogue-derived unit declarations and labelled final-response verification are
later integration steps. Computation currently runs over the existing JSONB
snapshot in the ledger process, not a row-level SQL query engine.

## Registered resource catalogue

The catalogue stores multiple non-overlapping table regions per sheet, each with
stable table and column IDs. It requires PostgreSQL's `pg_trgm` extension; the
migration account must be able to install it, or an administrator must install it
before the service starts.

- The application register/create-table operations register finite bounds whose
  first row has complete, unique text headers. Supply the observed sheet revision, name and
  optional description, grain, aliases, entity and period coverage.
- The application table-update operations require the table ID plus expected
  catalogue and source sheet revisions. Metadata and same-sheet bounds can change
  while IDs persist. Changed headers use explicit column-ID mapping in checked authoring.
- `tool_search_resources(query)` searches indexed metadata through exact names,
  aliases, full-text terms and trigrams. Model-provided `concepts` expand intent;
  optional required columns, entity and date act as hard filters. It returns up
  to 20 candidates, reasons, freshness and the first 16 columns per candidate.
- `tool_inspect_resource(table_id, column_offset, column_limit)` reads metadata
  and paginates columns (32 by default, at most 64). It does not load cell records.

Search and inspection share the 65,536-byte read budget. Narrow candidate limits
if metadata exceeds it. Register/update return compact commit confirmations, so
large schemas cannot cause a response-size failure after a successful write.
MCP exposes search and inspection only. Table registration and metadata updates
must use the application's checked authoring operations.

Search covers explicitly registered tables only. Descriptions, grain, entity and
period are caller-declared content; headers come from the source sheet. Any sheet
revision change marks old metadata stale. Refresh requires another checked update;
automatic detection, refresh, formula relationships and column remapping remain
pending in this MCP read contract. The main agent uses the application catalogue
interface. MCP requests remain limited to their authenticated owner's workbooks.

The application also offers owner-scoped HTTP discovery through
`POST /v1/resources/search` and `GET /v1/resources/{table_id}`. Those routes obtain
the user ID from the verified application JWT and join current workbook ownership
within the catalogue read. They reuse the same search ranking, schema pagination
and byte budget. MCP also verifies current workbook ownership; it does not accept
a model-provided user ID as authority.

## Calculations over registered tables

The application catalogue service also calculates sums/counts by stable table ID.
It checks current ownership, expected sheet revision, expected catalogue revision
and catalogue freshness against one database snapshot. The agent injects revisions
from its inspected working-set reference; model input cannot replace workbook IDs,
physical bounds or expected revisions. No additional MCP endpoint is introduced.

The resolved query and source identities accompany each result. Metrics include
stable column IDs as well as names. The registered-table path preserves stored
fractional JSON numbers as Decimal operands; it rejects inexact sums beyond 64
digits. Numeric group labels must fit the existing JSON scalar representation
without rounding. Unused columns and filtered-out operands do not block a total.
The existing unit, blank-value, numeric-text and group-count policies still apply.

A result describes the observed snapshot. It does not certify registration meaning,
perform formula recalculation or grant write permission. This path still loads the
whole sheet JSONB value; row-level query storage remains pending.

## Owned table append transactions

`LedgerStore.append_table_owned(user_id, request)` accepts a `TableAppend` with
stable table ID, observed sheet/catalogue revisions, a stable idempotency key and
up to 100 named records within a 65,536-byte request budget. Each record must
supply every registered column exactly; use explicit nulls for intended blanks.
All-blank records and formula expressions are rejected. Tables already containing
formula expressions also reject appends until formula propagation exists.

The transaction locks current workbook ownership, then the sheet and catalogue
entry. It appends after the table's last populated row, consumes reserved blank
rows first and expands only into empty cells. Registered region collisions and
unregistered occupied cells both block expansion. It does not shift other tables.
PostgreSQL patches the cells without rounding unchanged JSONB numbers. Stored-cell
verification, table bounds, record count, revisions and the receipt commit together.
Other registered tables on the sheet become stale under the existing sheet-wide
revision policy; the appended table's metadata remains current.

Idempotency keys are scoped by authenticated user. Replays require the exact
checked request, including its original revisions; changed payloads conflict.
Identical concurrent calls commit once. Replays recheck current workbook ownership.
Receipts survive table deletion while the owned workbook exists; deletion or
transfer of that workbook denies replay access. Receipts do not claim formula
recalculation or accounting validation. Retry callers must retain the original
request and key, including when a response is lost after commit.

The main agent prepares exact table append proposals through the application
operation interface and executes stored references. Durable tasks retain those
references and receipts for recovery. MCP does not expose this write path.


## Checked table authoring

`ledger.authoring` prepares and executes owner-checked table lifecycle operations
using the existing operation and approval store. It supports `create_table`,
`register_table`, `update_table`, `refresh_table` and `unregister_table`.

Creation writes literal headers into a wholly blank finite region and registers
it in the same transaction. Registration uses existing cells. Updates replace
metadata and same-sheet bounds without moving cells. Changed headers require an
explicit ordered mapping of old column IDs, with null for new columns; IDs cannot
repeat or come from another table. Refresh preserves unchanged headers and IDs.
Unregister requires human approval and preserves cells.

Each preparation creates a fresh operation reference. Save it and reuse it for
execution retries; a retry returns the original committed receipt under current
ownership. A later intentional registration after unregistering gets a new table
identity. Sheet and catalogue revision checks reject stale changes. Overlap or
header validation failures roll back both header writes and metadata changes.
Header writes preserve unrelated JSONB numeric values without float conversion.

The workbook-bound MCP registration/update handlers are disabled. Use the
application authoring tools for those changes. Native typed formulas have their
own checked operation path; automatic region inference and physical row/column
deletion are separate capabilities.
