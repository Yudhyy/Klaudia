"""Legacy ledger imports retain canonical core types, functions and values."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "append_contracts",
        "authoring_contracts",
        "resources",
        "errors",
        "evidence",
        "query",
        "grid",
        "calculations",
        "financial_contracts",
        "typed_contracts",
        "formula_engine",
    ],
)
def test_legacy_modules_reexport_the_same_core_definitions(module: str) -> None:
    canonical = import_module(f"ledger_core.{module}")
    legacy = import_module(f"ledger.{module}")
    mismatches = [
        name
        for name, definition in vars(canonical).items()
        if not name.startswith("__") and getattr(legacy, name, None) is not definition
    ]
    assert not mismatches, f"{module}: {mismatches}"
