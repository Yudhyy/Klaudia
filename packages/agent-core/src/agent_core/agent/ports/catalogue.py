"""Agent-owned read requests and failures, without ledger implementations."""

from datetime import date
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agent_core.agent.ports.calculations import CheckedCalculation
from agent_core.agent.ports.financial import CheckedFinancialRequest

Name = Annotated[str, Field(min_length=1, max_length=256)]


class ResourceInspection(BaseModel):
    """Requested table and schema page, without authority fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    table_id: Name
    column_offset: Annotated[StrictInt, Field(ge=0)] = 0
    column_limit: Annotated[StrictInt, Field(ge=1, le=64)] = 32


class ResourceSearch(BaseModel):
    """Bounded lexical discovery with optional hard business/schema filters."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    intent: Annotated[str, Field(min_length=1, max_length=512)]
    concepts: Annotated[list[Name], Field(max_length=16)] = Field(default_factory=list)
    required_columns: Annotated[list[Name], Field(max_length=16)] = Field(
        default_factory=list
    )
    entity: Name | None = None
    on_date: date | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=20)] = 5


class CatalogueReader(Protocol):
    """Application reads that enforce current ownership and output budgets."""

    async def search(self, user_id: int, query: ResourceSearch) -> dict[str, Any]:
        """Search metadata owned by the server-supplied user.

        Args:
            user_id: Authenticated identity.
            query: Validated model intent and filters.

        Returns:
            Bounded candidates with ownership checked during the read.
        """
        ...

    async def inspect(self, user_id: int, query: ResourceInspection) -> dict[str, Any]:
        """Inspect metadata while rechecking current ownership.

        Args:
            user_id: Authenticated identity.
            query: Requested resource and schema page.

        Returns:
            Bounded metadata, schema page and revision evidence.
        """
        ...

    async def calculate(
        self, user_id: int, query: CheckedCalculation
    ) -> dict[str, Any]:
        """Calculate owned metrics against inspected source revisions.

        Args:
            user_id: Authenticated identity.
            query: Requested metrics and trusted observed revisions.

        Returns:
            Exact labelled metrics from one ownership-checked source snapshot.

        Raises:
            ResourceNotFoundError: Source is absent or foreign.
            RevisionConflictError: Data or metadata changed since inspection.
            ValueError: Inputs or evidence exceed calculation budgets.
        """
        ...

    async def financial_query(
        self, user_id: int, query: CheckedFinancialRequest
    ) -> dict[str, Any]:
        """Read financial evidence while rechecking every source.

        Args:
            user_id: Authenticated identity.
            query: Financial intent with observed revisions.

        Returns:
            Bounded labelled evidence from one owned snapshot.

        Raises:
            ResourceNotFoundError: Any source is absent or foreign.
            RevisionConflictError: Source or metadata observations changed.
            ValueError: Inputs or evidence exceed execution limits.
        """
        ...
