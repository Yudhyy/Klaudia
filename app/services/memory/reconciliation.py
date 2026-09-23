"""Read-only reconciliation under revision-bound human accounting policy."""

from datetime import date
from typing import Annotated, Any

from langchain_core.tools import StructuredTool
from pydantic import BeforeValidator, Field

from app.services.memory.contracts import DocumentPath, MAX_EXPECTED_REVISION
from app.services.memory.policy import PolicyLabel, ReconciliationPolicy
from app.services.memory.store import MemoryDocumentStore
from klaudia.core.agent.tools import CatalogueReader
from ledger.errors import RevisionConflictError
from ledger.evidence import bounded_evidence
from ledger.financial_contracts import (
    CheckedFinancialRequest,
    Keys,
    NumericPolicy,
    Page,
    Reconcile,
    SourceRevision,
    TableId,
)
from ledger.query import ColumnName
from ledger.resources import ResourceInspection


class PolicyReconciliation(Page):
    """Select source columns and declared scope; saved policy supplies the rules."""

    table_id: TableId
    right_table_id: TableId
    left_keys: Keys
    right_keys: Keys
    left_amount: ColumnName
    right_amount: ColumnName
    left_unit_column: ColumnName
    right_unit_column: ColumnName
    entity: PolicyLabel
    jurisdiction: PolicyLabel
    as_of: Annotated[date, BeforeValidator(ReconciliationPolicy.canonical_date)]
    policy_revision: Annotated[int, Field(strict=True, gt=0, le=MAX_EXPECTED_REVISION)]


class PolicyReconciliationTools:
    """Bind exact reads to authenticated identity and stored policy revisions."""

    def __init__(
        self, documents: MemoryDocumentStore, catalogue: CatalogueReader, user_id: int
    ) -> None:
        """Connect existing context and financial readers for one owner.

        Args:
            documents: Current policy store.
            catalogue: Ownership-checked financial source reader.
            user_id: Authenticated identity supplied by the server.
        """
        self._documents = documents
        self._catalogue = catalogue
        self._user_id = user_id
        self.tool = StructuredTool.from_function(
            coroutine=self.reconcile,
            name="reconcile_with_policy",
            description="Reconcile all rows in two owned registered tables under saved structured accounting policy. Read /accounting-policy.md first and supply its revision. Requires explicit unit columns and matching table entities. as_of selects policy validity, not a row date filter. Jurisdiction is caller-declared. Read-only; no legal compliance certification.",
            args_schema=PolicyReconciliation,
        )

    async def reconcile(self, **arguments: Any) -> dict[str, Any]:
        """Check policy before and after a revision-bound financial read.

        Args:
            arguments: Source columns, declared applicability and observed policy revision.

        Returns:
            Exact financial evidence with policy revision and checked scope.

        Raises:
            ValueError: Policy is missing or inapplicable, or columns are invalid.
            RevisionConflictError: Policy or source observations changed.
            ResourceNotFoundError: A table is absent or no longer owned.
        """
        request = PolicyReconciliation.model_validate(arguments)
        document = await self._documents.read(
            self._user_id, DocumentPath.ACCOUNTING_POLICY
        )
        policy = document.policy
        if document.status != "active" or policy is None:
            raise ValueError("An active structured accounting policy is required")
        if document.revision != request.policy_revision:
            raise RevisionConflictError("Policy revision changed; read policy again")
        if (
            request.entity != policy.entity
            or request.jurisdiction != policy.jurisdiction
        ):
            raise ValueError("Requested entity or jurisdiction does not match policy")
        if not policy.effective_from <= request.as_of <= policy.effective_until:
            raise ValueError("Requested as_of date is outside the policy period")
        sources = []
        for identity in dict.fromkeys((request.table_id, request.right_table_id)):
            descriptor = await self._catalogue.inspect(
                self._user_id, ResourceInspection(table_id=identity)
            )
            if descriptor["entity"] != policy.entity:
                raise ValueError("Both source tables must declare the policy entity")
            if descriptor["freshness"] != "current":
                raise RevisionConflictError(
                    "Source catalogue is stale; refresh before reconciliation"
                )
            sources.append(
                SourceRevision(
                    table_id=identity,
                    sheet_revision=descriptor["current_sheet_revision"],
                    catalogue_revision=descriptor["catalogue_revision"],
                )
            )
        query = Reconcile(
            operation="reconcile",
            right_table_id=request.right_table_id,
            left_keys=request.left_keys,
            right_keys=request.right_keys,
            null_keys=policy.null_keys,
            left_amount=request.left_amount,
            right_amount=request.right_amount,
            tolerance=policy.tolerance,
            offset=request.offset,
            limit=request.limit,
            policies=NumericPolicy(
                numeric_text=policy.numeric_text,
                null_amounts=policy.null_amounts,
                unit=policy.unit,
                left_unit_column=request.left_unit_column,
                right_unit_column=request.right_unit_column,
            ),
        )
        evidence = await self._catalogue.financial_query(
            self._user_id,
            CheckedFinancialRequest(
                table_id=request.table_id,
                query=query,
                sources=sources,
            ),
        )
        current = await self._documents.read(
            self._user_id, DocumentPath.ACCOUNTING_POLICY
        )
        if current.revision != document.revision or current.status != "active":
            raise RevisionConflictError(
                "Policy changed during reconciliation; read policy again"
            )
        return bounded_evidence(
            {
                **evidence,
                "accounting_validation": "policy_parameters_checked",
                "policy_evidence": {
                    "path": document.path.value,
                    "revision": document.revision,
                    "parameters": policy.model_dump(mode="json"),
                    "as_of": request.as_of.isoformat(),
                    "row_scope": "all_registered_rows",
                    "entity_verification": "both_registered_table_descriptors",
                    "jurisdiction_verification": "caller_declaration_only",
                    "unit_verification": "both_source_unit_columns",
                    "rounding": "not_applied_exact_difference",
                },
            }
        )
