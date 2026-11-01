"""Reconciliation intent; the backend owns saved policy and financial rules."""

from datetime import date
from typing import Annotated, Any, Protocol

from pydantic import Field, field_validator

from ledger_core.financial_contracts import Keys, Page, TableId
from ledger_core.query import ColumnName

PolicyLabel = Annotated[str, Field(min_length=1, max_length=160)]
MAX_POLICY_REVISION = 2**63 - 2


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
    as_of: date
    policy_revision: Annotated[int, Field(strict=True, gt=0, le=MAX_POLICY_REVISION)]

    @field_validator("as_of", mode="before")
    @classmethod
    def canonical_date(cls, value: object) -> object:
        """Accept only calendar dates or exact ISO date text.

        Args:
            value: External date value.

        Returns:
            Validated date input.

        Raises:
            ValueError: The input is not a calendar date.
        """
        if type(value) is date:
            return value
        if (
            type(value) is not str
            or len(value) != 10
            or date.fromisoformat(value).isoformat() != value
        ):
            raise ValueError("Policy dates require YYYY-MM-DD")
        return value


class PolicyReconciler(Protocol):
    """Return exact evidence only after current policy and source checks."""

    async def reconcile(self, request: PolicyReconciliation) -> dict[str, Any]:
        """Read under saved policy with ownership, revision and evidence checks.

        Args:
            request: Validated source intent and observed policy revision.

        Returns:
            Exact financial evidence and checked policy scope.

        Raises:
            ValueError: Policy is missing or does not apply, or evidence is too large.
            RevisionConflictError: Policy or source revisions changed.
            ResourceNotFoundError: A source is missing or foreign.
        """
        ...
