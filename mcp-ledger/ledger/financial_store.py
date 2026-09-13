"""Ownership and revision checks for single-snapshot financial execution."""

import json
from typing import Any

from ledger import grid
from ledger.calculations import decode_calculation_grid
from ledger.connections import ConnectionProvider
from ledger.errors import RevisionConflictError
from ledger.evidence import bounded_evidence
from ledger.financial import execute_financial
from ledger.financial_contracts import (
    Aging,
    CheckedFinancialRequest,
    Comparison,
    Join,
    Lookup,
    Records,
    source_ids,
)
from ledger.query import MAX_QUERY_CELLS
from ledger.resources import ResourceNotFoundError


def source_columns(request: CheckedFinancialRequest) -> dict[str, set[str]]:
    """Select all referenced columns for stable-ID source evidence.

    Args:
        request: Financial intent with checked source identities.

    Returns:
        Referenced column names grouped by table identity.
    """
    query = request.query
    selected: dict[str, set[str]] = {
        table_id: set() for table_id in source_ids(request)
    }
    left = selected[request.table_id]
    if isinstance(query, (Records, Lookup)):
        left.update(query.columns)
        left.update(predicate.column for predicate in query.filters)
        if isinstance(query, Records):
            left.update(order.column for order in query.sort)
    elif isinstance(query, Aging):
        left.update((query.amount, query.due_date))
        if query.policies.left_unit_column:
            left.add(query.policies.left_unit_column)
    else:
        right = selected[query.right_table_id]
        left.update(query.left_keys)
        right.update(query.right_keys)
        if isinstance(query, Join):
            left.update(query.columns)
            right.update(query.right_columns)
        elif isinstance(query, Comparison):
            left.add(query.left_amount)
            right.add(query.right_amount)
            if query.policies.left_unit_column:
                left.add(query.policies.left_unit_column)
            if query.policies.right_unit_column:
                right.add(query.policies.right_unit_column)
    return selected


async def execute_owned(
    pool: ConnectionProvider, user_id: int, request: CheckedFinancialRequest
) -> dict[str, Any]:
    """Read every owned source in one SQL snapshot and return bounded evidence.

    Args:
        pool: Ledger connection provider.
        user_id: Authenticated identity supplied by the application.
        request: Intent bound to inspected source revisions.

    Returns:
        Financial results with checked source and stable column identities.

    Raises:
        ResourceNotFoundError: Any source is absent or foreign.
        RevisionConflictError: A source or catalogue observation is stale.
        ValueError: Source bindings, query inputs or evidence exceed their limits.
    """
    request = request.model_copy(deep=True)
    identities = source_ids(request)
    revisions = {source.table_id: source for source in request.sources}
    if len(revisions) != len(request.sources) or set(revisions) != set(identities):
        raise ValueError("Checked revisions must cover each source exactly once")
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT r.resource_id, r.table_range, r.columns, r.revision AS catalogue_revision,
                   r.source_revision, s.sheet_id, s.workspace, s.revision AS sheet_revision, s.grid
            FROM ledger_resource r
            JOIN ledger_sheet s ON s.sheet_id = r.sheet_id
            JOIN ledger_spreadsheet w ON w.spreadsheet_id = s.workspace
            WHERE w.user_id = $1 AND r.resource_id = ANY($2::text[])
            """,
            user_id,
            list(identities),
        )
    if len(rows) != len(identities):
        raise ResourceNotFoundError("Table not found")
    selected = source_columns(request)
    regions, sources = {}, {}
    for row in rows:
        identity = row["resource_id"]
        observed = revisions[identity]
        if (
            row["sheet_revision"] != observed.sheet_revision
            or row["catalogue_revision"] != observed.catalogue_revision
            or row["source_revision"] != row["sheet_revision"]
        ):
            raise RevisionConflictError(
                "Financial source changed; inspect and refresh stale catalogue metadata"
            )
        grid.validate_bounded_range(row["table_range"], MAX_QUERY_CELLS)
        regions[identity] = grid.slice_range(
            decode_calculation_grid(row["grid"]), row["table_range"]
        )
        sources[identity] = {
            "table_id": identity,
            "spreadsheet_id": row["workspace"],
            "sheet_id": row["sheet_id"],
            "range": row["table_range"],
            "sheet_revision": row["sheet_revision"],
            "catalogue_revision": row["catalogue_revision"],
            "columns": [
                {"name": column["name"], "column_id": column["column_id"]}
                for column in json.loads(row["columns"])
                if column["name"] in selected[identity]
            ],
        }
    evidence = execute_financial(regions, request)
    # Checked revisions belong in source evidence, not in model intent.
    evidence["query"] = request.model_dump(mode="json", exclude={"sources"})
    return bounded_evidence(
        {"sources": [sources[identity] for identity in identities], **evidence}
    )
