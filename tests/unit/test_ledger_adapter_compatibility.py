"""Existing ledger imports retain the shared adapter and core definitions."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "approvals",
        "authoring",
        "catalogue",
        "connections",
        "financial_store",
        "operations",
        "store",
        "table_operations",
        "typed_cells",
        "typed_storage",
        "migrations",
        "migrations.authoring",
        "migrations.catalog",
        "migrations.cli",
        "migrations.metadata",
        "migrations.runtime",
    ],
)
def test_legacy_imports_share_the_canonical_adapter_definitions(module):
    """Keep one definition for each supported legacy symbol, including errors."""
    canonical = import_module(f"ledger_postgres.{module}")
    legacy = import_module(f"ledger.{module}")
    names = (
        ("LEDGER_HISTORY", "History", "upgrade_histories", "upgrade_ledger")
        if module == "migrations"
        else tuple(name for name in vars(canonical) if not name.startswith("__"))
    )
    mismatches = [
        name
        for name in names
        if getattr(legacy, name, None) is not getattr(canonical, name)
    ]
    assert not mismatches, f"{module}: {mismatches}"


def test_application_authoring_path_uses_the_shared_implementation():
    """Keep the application import while moving its ledger SQL to the adapter."""
    from klaudia_api.adapters.ledger.catalogue.authoring import (
        AuthoringService as previous,
    )
    from ledger_postgres.authoring_reads import AuthoringService

    assert previous is AuthoringService


def test_task_receipt_reader_uses_the_shared_implementation():
    """Keep task connection binding separate from ledger receipt SQL."""
    from klaudia_api.adapters.ledger.task_operations import (
        operation_evidence as previous,
    )
    from ledger_postgres.table_operations import operation_evidence

    assert previous is operation_evidence


def test_application_approval_path_uses_the_shared_implementation():
    """Keep human-decision routing while sharing the concrete ledger adapter."""
    from klaudia_api.adapters.ledger.approvals import CheckedApprovals as previous
    from ledger_postgres.approval_service import CheckedApprovals

    assert previous is CheckedApprovals
