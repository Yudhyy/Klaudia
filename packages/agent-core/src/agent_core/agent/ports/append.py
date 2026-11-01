"""Agent-selected records and inspected revisions for checked ledger appends."""

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

CellValue = StrictStr | StrictInt | StrictFloat | StrictBool | None
RecordField = Annotated[StrictStr, Field(min_length=1, max_length=256)]
TableRecord = Annotated[
    dict[RecordField, CellValue], Field(min_length=1, max_length=256)
]


class TableAppendProposal(BaseModel):
    """Named records bound to server-observed table revisions before key assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    table_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)]
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)]
    records: Annotated[list[TableRecord], Field(min_length=1, max_length=100)]
