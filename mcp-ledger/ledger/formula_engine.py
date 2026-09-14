"""Bounded native decimal formulas with explicit dependencies and rounding."""

from decimal import Context, Decimal, DecimalException, Inexact, localcontext
from fractions import Fraction
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ENGINE_VERSION = "native-decimal-v1"
MAX_TYPED_CELLS = 256
PRECISION = 64
DecimalText = Annotated[
    str,
    Field(
        strict=True, min_length=1, max_length=128, pattern=r"^[+-]?[0-9]+(?:\.[0-9]+)?$"
    ),
]
CellId = Annotated[str, Field(strict=True, min_length=1, max_length=80)]


class Contract(BaseModel):
    """Forbid undeclared fields and mutation of validated contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LiteralOperand(Contract):
    """A decimal literal retained verbatim without binary conversion."""

    literal: DecimalText


class CellOperand(Contract):
    """An explicit dependency on a stable managed cell identity."""

    cell_id: CellId


class Rounding(Contract):
    """Explicit financial scale and a single final rounding mode."""

    places: Literal[2, 4]
    mode: Literal["ROUND_HALF_EVEN", "ROUND_HALF_UP", "ROUND_UP", "ROUND_DOWN"]


class Formula(Contract):
    """One native operation; complex formulas compose through cell dependencies."""

    operation: Literal[
        "add", "subtract", "multiply", "divide", "sum", "round", "roundup", "rounddown"
    ]
    operands: Annotated[
        list[LiteralOperand | CellOperand], Field(min_length=1, max_length=64)
    ]
    rounding: Rounding | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "Formula":
        """Check arity and the declared rounding function.

        Returns:
            Validated formula.

        Raises:
            ValueError: Arity or rounding contradicts the operation.
        """
        if (
            self.operation in ("add", "subtract", "multiply", "divide")
            and len(self.operands) != 2
        ):
            raise ValueError("Binary arithmetic requires two operands")
        if self.operation in ("round", "roundup", "rounddown"):
            if len(self.operands) != 1 or self.rounding is None:
                raise ValueError(
                    "Rounding functions require one operand and an explicit scale"
                )
        expected = {"roundup": "ROUND_UP", "rounddown": "ROUND_DOWN"}.get(
            self.operation
        )
        if expected and self.rounding.mode != expected:
            raise ValueError("Rounding mode must match the rounding function")
        if (
            self.rounding
            and not expected
            and self.rounding.mode not in ("ROUND_HALF_EVEN", "ROUND_HALF_UP")
        ):
            raise ValueError("Financial rounding requires half-even or half-up")
        return self

    def dependencies(self) -> tuple[str, ...]:
        """Return the exact stable-cell dependencies in operand order.

        Returns:
            Unique dependency identities.
        """
        return tuple(
            dict.fromkeys(
                operand.cell_id
                for operand in self.operands
                if isinstance(operand, CellOperand)
            )
        )


def decimal_value(text: str) -> Decimal:
    """Validate bounded exact decimal operands.

    Args:
        text: A declared decimal string.

    Returns:
        Finite decimal within the engine's digit and exponent budget.

    Raises:
        ValueError: The operand exceeds the numeric contract.
    """
    value = Decimal(text)
    if (
        not value.is_finite()
        or len(value.as_tuple().digits) > PRECISION
        or abs(value.as_tuple().exponent) > 128
        or abs(value.adjusted()) > 128
    ):
        raise ValueError("Decimal exceeds the 64-digit or exponent budget")
    return value


def round_fraction(value: Fraction, policy: Rounding) -> str:
    """Round a rational result once, including negative financial values.

    Args:
        value: Exact rational result.
        policy: Explicit mode and decimal places.

    Returns:
        Decimal text retaining the requested scale.

    Raises:
        ValueError: The rounded result exceeds the digit budget.
    """
    scaled = value * 10**policy.places
    magnitude = abs(scaled)
    whole, remainder = divmod(magnitude.numerator, magnitude.denominator)
    twice = remainder * 2
    increment = (
        (policy.mode == "ROUND_UP" and remainder != 0)
        or (policy.mode == "ROUND_HALF_UP" and twice >= magnitude.denominator)
        or (
            policy.mode == "ROUND_HALF_EVEN"
            and (
                twice > magnitude.denominator
                or (twice == magnitude.denominator and whole % 2)
            )
        )
    )
    whole += int(bool(increment))
    if len(str(whole)) > PRECISION:
        raise ValueError("Rounded result exceeds the 64-digit budget")
    return format(
        Decimal(-whole if scaled < 0 else whole).scaleb(-policy.places),
        f".{policy.places}f",
    )


def calculate_formula(
    formula: Formula, available: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Evaluate one formula from current dependency values.

    Args:
        formula: Validated native operation.
        available: Input and already-calculated dependency evidence.

    Returns:
        Current decimal value or explicit calculation failure.
    """
    if any(
        available[identity]["status"] != "current"
        for identity in formula.dependencies()
    ):
        return {"status": "failed", "value": None, "error": "dependency_failed"}
    try:
        operands = [
            decimal_value(
                operand.literal
                if isinstance(operand, LiteralOperand)
                else available[operand.cell_id]["value"]
            )
            for operand in formula.operands
        ]
        rational = Fraction(operands[0])
        if formula.operation == "add":
            rational += Fraction(operands[1])
        elif formula.operation == "subtract":
            rational -= Fraction(operands[1])
        elif formula.operation == "multiply":
            rational *= Fraction(operands[1])
        elif formula.operation == "divide":
            rational /= Fraction(operands[1])
        elif formula.operation == "sum":
            rational = sum((Fraction(value) for value in operands), Fraction(0))
        if formula.rounding:
            value = round_fraction(rational, formula.rounding)
        else:
            value = format(
                Decimal(rational.numerator) / Decimal(rational.denominator), "f"
            )
            decimal_value(value)
        return {"status": "current", "value": value, "error": None}
    except (DecimalException, ValueError, ZeroDivisionError):
        return {
            "status": "failed",
            "value": None,
            "error": "invalid_or_inexact_arithmetic",
        }


def evaluate_graph(
    inputs: dict[str, str], formulas: dict[str, Formula]
) -> dict[str, dict[str, Any]]:
    """Evaluate a bounded acyclic graph using exact decimal and rational values.

    Args:
        inputs: Decimal values keyed by stable cell identity.
        formulas: Native formulas keyed by their computed cell identity.

    Returns:
        Formula results with honest current or failed status.

    Raises:
        ValueError: Identities, dependencies or graph size are invalid.
    """
    if len(inputs) + len(formulas) > MAX_TYPED_CELLS or inputs.keys() & formulas.keys():
        raise ValueError("Invalid or oversized formula graph")
    known = inputs.keys() | formulas.keys()
    graph = {identity: formula.dependencies() for identity, formula in formulas.items()}
    if any(
        dependency not in known
        for dependencies in graph.values()
        for dependency in dependencies
    ):
        raise ValueError("Unknown formula dependency")
    try:
        order = tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ValueError("Formula dependency cycle") from error
    with localcontext(Context(prec=PRECISION, Emax=128, Emin=-128)) as context:
        context.traps[Inexact] = True
        available = {
            identity: {"status": "current", "value": value, "error": None}
            for identity, value in inputs.items()
        }
        for identity in order:
            if identity in formulas:
                available[identity] = calculate_formula(formulas[identity], available)
    return {identity: available[identity] for identity in formulas}
