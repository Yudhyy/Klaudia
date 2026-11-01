"""Agent table-authoring intent; ledger validates action and placement rules."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agent_core.agent.ports.catalogue import Name


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


class AuthoringProposal(BaseModel):
    """One explicit table lifecycle change with observed revision preconditions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal[
        "create_table",
        "register_table",
        "update_table",
        "refresh_table",
        "unregister_table",
    ]
    definition: TableRegistration | None = None
    table_id: Name | None = None
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)] | None = None
    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)] | None = None
    headers: Annotated[list[Name], Field(min_length=1, max_length=256)] | None = None
    column_ids: Annotated[list[Name | None], Field(max_length=256)] | None = None
