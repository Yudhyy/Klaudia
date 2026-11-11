"""Explicit applicability and exact matching rules for saved reconciliation policy."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PolicyLabel = Annotated[str, Field(min_length=1, max_length=160)]


class ReconciliationPolicy(BaseModel):
    """Bind reconciliation rules to one declared entity, jurisdiction and period."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    entity: PolicyLabel
    jurisdiction: PolicyLabel
    effective_from: date
    effective_until: date
    unit: Annotated[str, Field(min_length=1, max_length=64)]
    numeric_text: Literal["reject", "decimal"]
    null_amounts: Literal["reject", "exclude"]
    null_keys: Literal["reject", "never_match"]
    tolerance: Annotated[Decimal, Field(ge=0, max_digits=64, decimal_places=32)]

    @field_validator("entity", "jurisdiction", "unit")
    @classmethod
    def explicit_label(cls, value: str) -> str:
        """Reject blank, padded or unstoreable labels.

        Args:
            value: Declared policy label.

        Returns:
            Unchanged exact label.

        Raises:
            ValueError: The label is blank, padded or contains a null byte.
        """
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("Policy labels must be nonblank, unpadded text")
        return value

    @field_validator("effective_from", "effective_until", mode="before")
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

    @field_validator("tolerance", mode="before")
    @classmethod
    def exact_tolerance(cls, value: object) -> object:
        """Require exact decimal text rather than a JSON floating point value.

        Args:
            value: Requested tolerance.

        Returns:
            Exact input for decimal validation.

        Raises:
            ValueError: The caller supplied an inexact or coerced numeric type.
        """
        if not isinstance(value, (str, Decimal)):
            raise ValueError("Tolerance requires exact decimal text")
        return value

    @model_validator(mode="after")
    def ordered_dates(self) -> "ReconciliationPolicy":
        """Require a nonempty inclusive policy period.

        Returns:
            Validated policy.

        Raises:
            ValueError: The end date precedes the start date.
        """
        if self.effective_until < self.effective_from:
            raise ValueError("Policy end date precedes its start date")
        return self
