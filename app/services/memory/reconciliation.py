"""Read-only reconciliation under revision-bound human accounting policy."""

from typing import Any

from ledger_core.errors import RevisionConflictError
from ledger_core.evidence import bounded_evidence
from ledger_core.financial_contracts import (
    CheckedFinancialRequest,
    NumericPolicy,
    Reconcile,
    SourceRevision,
)
from ledger_core.resources import ResourceInspection

from app.services.catalogue.service import CatalogueService
from app.services.memory.contracts import DocumentPath
from app.services.memory.store import MemoryDocumentStore
from agent_core.agent.ports.reconciliation import PolicyReconciliation


class PolicyReconciler:
    """Bind exact reads to authenticated identity and stored policy revisions."""

    def __init__(
        self, documents: MemoryDocumentStore, catalogue: CatalogueService, user_id: int
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

    async def reconcile(self, request: PolicyReconciliation) -> dict[str, Any]:
        """Check policy before and after a revision-bound financial read.

        Args:
            request: Validated source intent and observed policy revision.

        Returns:
            Exact financial evidence with policy revision and checked scope.

        Raises:
            ValueError: Policy is missing or inapplicable, or columns are invalid.
            RevisionConflictError: Policy or source observations changed.
            ResourceNotFoundError: A table is absent or no longer owned.
        """
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
