"""On-demand sheet evidence and checked table-authoring proposals."""

from typing import Annotated, Any, Protocol

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ledger.authoring import AuthoringProposal
from klaudia.core.agent.writes import WriteTools


class AuthoringReader(Protocol):
    """Server-owned placement reads used before an explicit table change."""

    async def sheets(
        self, user_id: int, *, workbook_id: str | None = None, offset: int = 0
    ) -> dict[str, Any]:
        """Return a bounded page of currently owned sheet identities.

        Args:
            user_id: Server-authenticated owner.
            workbook_id: Optional destination filter.
            offset: Number of sheets already read.

        Returns:
            Sheet identities and a continuation offset.
        """
        ...

    async def inspect(
        self, user_id: int, sheet_id: int, table_range: str
    ) -> dict[str, Any]:
        """Return a finite owned region with current source revision evidence.

        Args:
            user_id: Server-authenticated owner.
            sheet_id: Requested source identity.
            table_range: Bounded A1 placement region.

        Returns:
            Source revision and current placement evidence.
        """
        ...


class SheetSearch(BaseModel):
    """Optional authoring destination filter with no permission fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    workbook_id: str | None = Field(default=None, min_length=1, max_length=256)
    offset: Annotated[StrictInt, Field(ge=0, le=100000)] = 0


class SheetRegion(BaseModel):
    """A finite placement region selected for inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sheet_id: Annotated[StrictInt, Field(gt=0)]
    table_range: str = Field(min_length=1, max_length=64)


class AuthoringTools:
    """Bind placement reads and proposal preparation to one authenticated task."""

    def __init__(
        self, reader: AuthoringReader, writes: WriteTools, user_id: int
    ) -> None:
        """Build tools using server-selected readers, executor and identity.

        Args:
            reader: Owned placement reads.
            writes: Task-local proposal and receipt tracking.
            user_id: Authenticated owner supplied by the server.
        """
        self._reader = reader
        self._user_id = user_id
        self.tools = (
            StructuredTool.from_function(
                coroutine=self.sheets,
                name="list_authoring_sheets",
                description="List up to 20 owned sheet identities for explicit table authoring. Use only when creating or registering a table; use search_resources for existing financial tables.",
                args_schema=SheetSearch,
            ),
            StructuredTool.from_function(
                coroutine=self.inspect,
                name="inspect_sheet_region",
                description="Inspect up to 4096 cells and overlapping tables before authoring. Returns the current sheet revision; inspection is not permission to overwrite cells.",
                args_schema=SheetRegion,
            ),
            StructuredTool.from_function(
                coroutine=writes.prepare_authoring,
                name="prepare_table_authoring",
                description="Load table-authoring first. Prepare create/register/update/refresh/unregister using exact inspected source revisions. Updates change catalogue metadata, not stored headers or financial records. Unregister always requires human approval and preserves cells. Execute the returned original reference.",
                args_schema=AuthoringProposal,
            ),
        )

    async def sheets(self, **arguments: Any) -> dict[str, Any]:
        """Read authoring destinations with the server's authenticated identity.

        Args:
            arguments: Optional workbook filter and page offset.

        Returns:
            Owned sheet identities, a continuation offset and access limits.
        """
        query = SheetSearch.model_validate(arguments)
        sheets = await self._reader.sheets(self._user_id, **query.model_dump())
        return {
            **sheets,
            "access_scope": "authenticated_owner_only",
            "inaccessible_resources": "existence_and_contents_unknown",
        }

    async def inspect(self, **arguments: Any) -> dict[str, Any]:
        """Inspect placement evidence without accepting caller-owned scope.

        Args:
            arguments: Requested sheet identity and finite A1 region.

        Returns:
            Current owned placement evidence.
        """
        query = SheetRegion.model_validate(arguments)
        return await self._reader.inspect(
            self._user_id, query.sheet_id, query.table_range
        )
