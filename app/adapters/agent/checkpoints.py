"""Interpret saved agent state for replay access checks and public task progress."""

import json
from typing import Any

import asyncpg
from pydantic import ValidationError

from agent_core.agent.writes import ExecuteOperation
from app.services.memory.contracts import DocumentPath
from app.services.memory.ports import DocumentReader
from ledger_core.errors import RevisionConflictError
from ledger_core.resources import ResourceNotFoundError


async def check_saved_access(
    connection: asyncpg.Connection,
    record: dict[str, Any],
    documents: DocumentReader,
) -> None:
    """Recheck all saved tool sources before replaying their evidence.

    Args:
        connection: Connection holding the task lock.
        record: Persisted task and original input.
        documents: Owner-scoped memory reads on the task connection.

    Raises:
        ResourceNotFoundError: A saved source is no longer owned.
        RevisionConflictError: Saved policy is no longer current and active.
    """
    state = json.loads(record["checkpoint"]) if record["checkpoint"] else {}
    workbooks = set()
    document_ids = set(json.loads(record["input_payload"])["turn"]["document_ids"])
    for name, _, encoded in state.get("evidence", []):
        evidence = json.loads(encoded)
        if name == "list_authoring_sheets":
            workbooks.update(item["spreadsheet_id"] for item in evidence["sheets"])
        elif name == "inspect_sheet_region":
            workbooks.add(evidence["spreadsheet_id"])
        elif name == "inspect_typed_workbook":
            workbooks.add(evidence["workbook_id"])
        elif name == "search_resources":
            workbooks.update(item["spreadsheet_id"] for item in evidence["candidates"])
        elif name == "inspect_resource":
            workbooks.add(evidence["spreadsheet_id"])
        elif name in {"financial_query", "reconcile_with_policy"}:
            workbooks.update(source["spreadsheet_id"] for source in evidence["sources"])
            if name == "reconcile_with_policy":
                policy = await documents.read(
                    record["user_id"], DocumentPath.ACCOUNTING_POLICY
                )
                if (
                    policy.status != "active"
                    or policy.revision != evidence["policy_evidence"]["revision"]
                ):
                    raise RevisionConflictError(
                        "Saved reconciliation policy changed; start a new task"
                    )
        elif name == "execute_operation" and evidence.get("status") == "committed":
            workbooks.add(evidence["target"]["spreadsheet_id"])
        elif name == "search_documents":
            document_ids.update(item["file_id"] for item in evidence["documents"])
        elif name == "read_document_page":
            document_ids.add(evidence["file_id"])
    owned_workbooks = await connection.fetchval(
        "SELECT count(*) FROM ledger_spreadsheet WHERE user_id = $1 AND spreadsheet_id = ANY($2::text[])",
        record["user_id"],
        list(workbooks),
    )
    owned_documents = await connection.fetchval(
        "SELECT count(*) FROM metadata_file f JOIN session s ON s.session_id = f.session_id WHERE f.user_id = $1 AND s.user_id = $1 AND f.id = ANY($2::int[])",
        record["user_id"],
        list(document_ids),
    )
    if owned_workbooks != len(workbooks) or owned_documents != len(document_ids):
        raise ResourceNotFoundError("Saved task source is no longer available")


def task_progress(record: dict[str, Any]) -> dict[str, Any]:
    """Select public progress from the agent-owned checkpoint format.

    Args:
        record: Owned task row with its original serialized continuation.

    Returns:
        Status, tool names and original operation references without model state.
    """
    state = json.loads(record["checkpoint"]) if record["checkpoint"] else {}
    references = list(state.get("operation_references", []))
    for call in state.get("pending_calls", []):
        if call["name"] == "execute_operation":
            try:
                reference = ExecuteOperation.model_validate(call["args"]).operation_ref
            except ValidationError:
                continue
            references.append(reference)
    references = list(dict.fromkeys(references))
    return {
        "task_id": record["task_id"],
        "session_id": record["session_id"],
        "status": record["status"],
        "tools_completed": state.get("calls", []),
        "pending_tools": [call["name"] for call in state.get("pending_calls", [])],
        "operation_references": references,
    }
