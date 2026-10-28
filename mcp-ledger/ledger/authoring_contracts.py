"""Table lifecycle proposals independent of persistence and transport."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ledger.resources import Name, TableRegistration


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

    @model_validator(mode="after")
    def check_action_fields(self) -> "AuthoringProposal":
        """Reject missing or inapplicable action fields.

        Returns:
            The complete action contract.

        Raises:
            ValueError: The action's fields are incomplete or ambiguous.
        """
        needs_definition = self.action in (
            "create_table",
            "register_table",
            "update_table",
        )
        existing = self.action in ("update_table", "refresh_table", "unregister_table")
        if needs_definition != (self.definition is not None):
            raise ValueError("This action requires exactly its table definition")
        if existing != (self.table_id is not None) or existing != (
            self.expected_catalogue_revision is not None
        ):
            raise ValueError(
                "Existing-table actions require table ID and catalogue revision"
            )
        if (not needs_definition) != (self.expected_sheet_revision is not None):
            raise ValueError(
                "Supply the sheet revision in the definition or refresh/unregister request"
            )
        if (self.action == "create_table") != (self.headers is not None):
            raise ValueError("Only create_table requires headers")
        if self.column_ids is not None and self.action != "update_table":
            raise ValueError("Explicit column mapping belongs to update_table")
        return self
