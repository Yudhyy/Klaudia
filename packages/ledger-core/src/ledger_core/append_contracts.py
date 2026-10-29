"""Literal append contracts shared by agent tools and ledger execution."""

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
AppendRow = Annotated[list[CellValue], Field(min_length=1, max_length=256)]
RecordField = Annotated[StrictStr, Field(min_length=1, max_length=256)]
TableRecord = Annotated[
    dict[RecordField, CellValue], Field(min_length=1, max_length=256)
]


class AppendRows(BaseModel):
    """A bounded literal-value append based on an observed sheet revision."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    sheet_id: Annotated[StrictInt, Field(gt=0)]
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    idempotency_key: Annotated[
        StrictStr, Field(min_length=1, max_length=128, pattern=r"\S")
    ]
    rows: Annotated[list[AppendRow], Field(min_length=1, max_length=1000)]


class TableAppendProposal(BaseModel):
    """Named records bound to server-observed table revisions before key assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    table_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)]
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)]
    records: Annotated[list[TableRecord], Field(min_length=1, max_length=100)]


class TableAppend(TableAppendProposal):
    """An exact checked append carrying its execution retry identity."""

    idempotency_key: Annotated[
        StrictStr, Field(min_length=1, max_length=128, pattern=r"\S")
    ]
