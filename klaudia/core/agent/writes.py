"""Optional append tools retaining server-checked proposals and commit evidence."""

from copy import deepcopy
from typing import Annotated, Any, Protocol

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from klaudia.core.agent.tools import DiscoveryTools
from ledger.evidence import bounded_evidence
from ledger.errors import RevisionConflictError
from ledger.table_operations import TableAppendProposal, TableRecord


class OperationExecutor(Protocol):
    """Application boundary enforcing current ownership for proposals and execution."""

    async def prepare(
        self, user_id: int, request: TableAppendProposal
    ) -> dict[str, str]:
        """Persist checked records under authenticated ownership.

        Args:
            user_id: Server-authenticated identity.
            request: Records and inspected revisions before key assignment.

        Returns:
            Stored proposal reference without claiming a committed write.
        """
        ...

    async def execute(self, user_id: int, operation_ref: str) -> dict[str, Any]:
        """Execute the exact stored request under current ownership.

        Args:
            user_id: Server-authenticated identity.
            operation_ref: Stored proposal reference.

        Returns:
            Committed operation receipt, including exact replay.
        """
        ...


class PrepareTableAppend(BaseModel):
    """Model-selected records with no identity, revisions or retry key."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    table_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    records: Annotated[list[TableRecord], Field(min_length=1, max_length=100)]


class ExecuteOperation(BaseModel):
    """Select a stored proposal without changing its checked request."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_ref: Annotated[StrictStr, Field(min_length=1, max_length=128)]


class WriteTools:
    """Bind optional write capabilities to one task's authenticated identity."""

    def __init__(self, discovery: DiscoveryTools, executor: OperationExecutor) -> None:
        """Use inspected references and an explicitly supplied execution service.

        Args:
            discovery: Task-local authenticated resource evidence.
            executor: Application boundary persisting and executing requests.
        """
        self._discovery = discovery
        self._executor = executor
        self._receipts: dict[str, dict[str, Any]] = {}
        self._references: dict[str, None] = {}
        self.tools = (
            StructuredTool.from_function(
                coroutine=self.prepare,
                name="prepare_table_append",
                description="Prepare complete named records for an inspected current table. Stores an exact proposal without changing cells. Identical records at identical revisions reuse the reference. Load table-append first.",
                args_schema=PrepareTableAppend,
            ),
            StructuredTool.from_function(
                coroutine=self.execute,
                name="execute_operation",
                description="Execute a prepared append reference, or retry that same reference after a lost response. Rechecks current ownership. Only a committed receipt proves the append; preparation is not approval or completion.",
                args_schema=ExecuteOperation,
            ),
        )

    @property
    def operation_references(self) -> tuple[str, ...]:
        """Expose observed proposal references for caller-managed recovery.

        Returns:
            Prepared or executed references, including uncertain executions.
        """
        return tuple(self._references)

    @property
    def receipts(self) -> tuple[dict[str, Any], ...]:
        """Return separate copies of committed evidence observed in this run.

        Returns:
            One receipt per committed operation, including replay observations.
        """
        return tuple(deepcopy(receipt) for receipt in self._receipts.values())

    def restore(self, references: list[str], receipts: list[dict[str, Any]]) -> None:
        """Retain prior operation evidence when continuing the same durable task.

        Args:
            references: Original server-persisted retry identities.
            receipts: Previously observed committed receipts.
        """
        self._references = dict.fromkeys(references)
        self._receipts = {
            receipt["operation_id"]: deepcopy(receipt) for receipt in receipts
        }

    async def prepare(self, **arguments: Any) -> dict[str, Any]:
        """Bind named records to revisions from the task's inspected reference.

        Args:
            arguments: Table identity and complete named records.

        Returns:
            A bounded durable proposal reference.

        Raises:
            ValueError: The table was not inspected or arguments are invalid.
            RevisionConflictError: The observed catalogue is stale.
            ResourceNotFoundError: Current ownership does not permit preparation.
        """
        proposal = PrepareTableAppend.model_validate(arguments)
        reference = self._discovery.reference(proposal.table_id)
        if reference.freshness != "current":
            raise RevisionConflictError(
                "Inspect current catalogue metadata before appending"
            )
        checked = TableAppendProposal(
            **proposal.model_dump(),
            expected_sheet_revision=reference.current_sheet_revision,
            expected_catalogue_revision=reference.catalogue_revision,
        )
        prepared_operation = await self._executor.prepare(
            self._discovery.context.user_id, checked
        )
        self._references[prepared_operation["operation_ref"]] = None
        return bounded_evidence(prepared_operation)

    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """Replay the persisted request and retain only committed evidence.

        Args:
            arguments: Stored operation reference with no replacement payload.

        Returns:
            The bounded committed receipt.

        Raises:
            ResourceNotFoundError: Reference or current ownership is absent.
            RevisionConflictError: An uncommitted proposal is stale.
            RuntimeError: The executor returned no committed receipt.
        """
        operation = ExecuteOperation.model_validate(arguments)
        self._references[operation.operation_ref] = None
        receipt = await self._executor.execute(
            self._discovery.context.user_id, operation.operation_ref
        )
        if receipt.get("status") != "committed":
            raise RuntimeError("Operation executor returned no committed receipt")
        self._receipts[receipt["operation_id"]] = deepcopy(receipt)
        await self._discovery.invalidate_sheet(receipt["target"]["sheet_id"])
        return bounded_evidence(receipt)
