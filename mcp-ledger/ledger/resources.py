"""Contracts for registered table identities and evidence-based discovery."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Name = Annotated[str, Field(min_length=1, max_length=256)]


class ResourceInspection(BaseModel):
    """Requested table and schema page, without authority fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    table_id: Name
    column_offset: Annotated[StrictInt, Field(ge=0)] = 0
    column_limit: Annotated[StrictInt, Field(ge=1, le=64)] = 32


class ResourceNotFoundError(Exception):
    """A table is absent from the bound workbook."""


class ResourceExistsError(Exception):
    """A registered table already occupies the requested region."""


class TableRegistration(BaseModel):
    """Caller-provided meaning attached to headers from an observed sheet revision."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    sheet_id: Annotated[StrictInt, Field(gt=0)]
    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)]
    table_range: Annotated[str, Field(min_length=1, max_length=64)]
    name: Name
    description: Annotated[str, Field(max_length=2000)] = ""
    grain: Annotated[str, Field(max_length=512)] = ""
    aliases: Annotated[list[Name], Field(max_length=20)] = Field(default_factory=list)
    entity: Name | None = None
    period_start: date | None = None
    period_end: date | None = None

    @model_validator(mode="after")
    def valid_period(self) -> "TableRegistration":
        """Check declared period ordering.

        Returns:
            The validated registration.

        Raises:
            ValueError: The declared period ends before it starts.
        """
        if (
            self.period_start
            and self.period_end
            and self.period_end < self.period_start
        ):
            raise ValueError("Period end must not precede its start")
        return self


class TableUpdate(BaseModel):
    """Revise a table without replacing its identity or silently remapping columns."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    table_id: Name
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)]
    definition: TableRegistration
    column_ids: Annotated[list[Name | None], Field(max_length=256)] | None = None


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
