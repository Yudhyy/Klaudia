"""Bounded financial intent with explicit matching and arithmetic policies."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ledger.query import ColumnName, EqualityFilter

Columns = Annotated[list[ColumnName], Field(min_length=1, max_length=16)]
Keys = Annotated[list[ColumnName], Field(min_length=1, max_length=3)]
TableId = Annotated[str, Field(min_length=1, max_length=256)]


class Contract(BaseModel):
    """Reject undeclared fields and non-finite values at the tool boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Page(Contract):
    """Bound result pages without changing the full calculation population."""

    offset: Annotated[int, Field(strict=True, ge=0, le=20_000)] = 0
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20


class Sort(Contract):
    """Declare ordering semantics rather than inferring numeric text or dates."""

    column: ColumnName
    kind: Literal["number", "text", "date"]
    direction: Literal["asc", "desc"]
    nulls: Literal["first", "last"]


class Selection(Contract):
    """Select exact columns and equality predicates from one registered table."""

    columns: Columns
    filters: Annotated[list[EqualityFilter], Field(max_length=8)] = Field(
        default_factory=list
    )


class Records(Selection, Page):
    """Return stable, typed record pages under declared sorting semantics."""

    operation: Literal["records"]
    sort: Annotated[list[Sort], Field(max_length=3)] = Field(default_factory=list)
    numeric_text: Literal["reject", "decimal"] = "reject"


class Lookup(Selection):
    """Resolve zero or one record, rejecting ambiguous matches."""

    operation: Literal["lookup"]
    missing: Literal["reject", "null"]
    filters: Annotated[list[EqualityFilter], Field(min_length=1, max_length=8)]


class Matching(Page):
    """Match exact composite keys without text coercion or fuzzy matching."""

    right_table_id: TableId
    left_keys: Keys
    right_keys: Keys
    null_keys: Literal["reject", "never_match"]

    @model_validator(mode="after")
    def validate_keys(self) -> "Matching":
        """Require paired, unique key columns.

        Returns:
            Validated matching request.

        Raises:
            ValueError: Key arity differs or a column repeats.
        """
        if len(self.left_keys) != len(self.right_keys):
            raise ValueError("Join keys must have equal lengths")
        if any(
            len(set(keys)) != len(keys) for keys in (self.left_keys, self.right_keys)
        ):
            raise ValueError("Key columns must be unique")
        return self


class Join(Matching):
    """Bound one-to-one or many-to-one joins; reject right-side duplication."""

    operation: Literal["join"]
    columns: Columns
    right_columns: Columns
    join_kind: Literal["inner", "left"]
    cardinality: Literal["one_to_one", "many_to_one"]


class NumericPolicy(Contract):
    """Declare amount parsing, blanks and units; invalid operands always reject."""

    numeric_text: Literal["reject", "decimal"]
    null_amounts: Literal["reject", "exclude"]
    unit: Annotated[str, Field(min_length=1, max_length=64)]
    left_unit_column: ColumnName | None
    right_unit_column: ColumnName | None


class Comparison(Matching):
    """Compare unique records with labelled left and right amount columns."""

    left_amount: ColumnName
    right_amount: ColumnName
    policies: NumericPolicy


class Reconcile(Comparison):
    """Compare left minus right using an absolute inclusive tolerance."""

    operation: Literal["reconcile"]
    tolerance: Annotated[Decimal, Field(ge=0, max_digits=64, decimal_places=32)]


class Variance(Comparison):
    """Report signed differences and explicitly rounded baseline percentages."""

    operation: Literal["variance"]
    direction: Literal["left_minus_right", "right_minus_left"]
    zero_baseline: Literal["reject", "null"]
    percentage_places: Annotated[int, Field(strict=True, ge=0, le=12)]
    rounding: Literal["ROUND_HALF_EVEN", "ROUND_HALF_UP", "ROUND_DOWN"]


class Aging(Contract):
    """Bucket signed outstanding amounts using ISO dates and calendar days."""

    operation: Literal["aging"]
    amount: ColumnName
    due_date: ColumnName
    as_of: date
    bucket_days: Annotated[
        list[Annotated[int, Field(strict=True, gt=0, le=36500)]],
        Field(min_length=1, max_length=12),
    ]
    policies: NumericPolicy

    @field_validator("as_of", mode="before")
    @classmethod
    def canonical_date(cls, value: object) -> object:
        """Reject timestamps and locale-dependent as-of inputs.

        Args:
            value: Supplied date value.

        Returns:
            Canonical ISO text or a date supplied by application code.

        Raises:
            ValueError: The input is not a full ISO date.
        """
        if type(value) is date:
            return value
        if (
            type(value) is not str
            or len(value) != 10
            or date.fromisoformat(value).isoformat() != value
        ):
            raise ValueError("As-of date requires YYYY-MM-DD text")
        return value

    @model_validator(mode="after")
    def validate_buckets(self) -> "Aging":
        """Require strictly increasing overdue upper bounds.

        Returns:
            Validated aging request.

        Raises:
            ValueError: Bounds repeat or are not increasing.
        """
        if self.bucket_days != sorted(set(self.bucket_days)):
            raise ValueError("Aging bucket days must be unique and increasing")
        if self.policies.right_unit_column is not None:
            raise ValueError(
                "Aging has no right source; right_unit_column must be null"
            )
        return self


FinancialQuery = Annotated[
    Records | Lookup | Join | Reconcile | Variance | Aging,
    Field(discriminator="operation"),
]


class FinancialRequest(Contract):
    """Model-selected identities and intent without access or revision fields."""

    table_id: TableId
    query: FinancialQuery


class SourceRevision(Contract):
    """Server-supplied table observation, rechecked against the read snapshot."""

    table_id: TableId
    sheet_revision: Annotated[int, Field(strict=True, ge=0)]
    catalogue_revision: Annotated[int, Field(strict=True, gt=0)]


class CheckedFinancialRequest(FinancialRequest):
    """Bind model intent to each inspected source without granting access."""

    sources: Annotated[list[SourceRevision], Field(min_length=1, max_length=2)]


def source_ids(request: FinancialRequest) -> tuple[str, ...]:
    """List distinct source identities in deterministic request order.

    Args:
        request: Validated financial intent.

    Returns:
        Left source and, for matching requests, the distinct right source.
    """
    if isinstance(request.query, Matching):
        return tuple(dict.fromkeys((request.table_id, request.query.right_table_id)))
    return (request.table_id,)
