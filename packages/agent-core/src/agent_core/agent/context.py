"""Server-bound task identity and observed resource references."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class TaskContext(BaseModel):
    """Server-supplied identity; the active workbook is context, not authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    user_id: StrictInt = Field(gt=0)
    active_workbook_id: str | None = None


@dataclass(frozen=True)
class ResourceReference:
    """Observed identity and revisions, never a grant of access or write approval."""

    table_id: str
    spreadsheet_id: str
    sheet_id: int
    table_range: str
    catalogue_revision: int
    source_revision: int
    current_sheet_revision: int
    freshness: Literal["current", "stale"]
