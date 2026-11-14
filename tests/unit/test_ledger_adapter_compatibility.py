"""Keep application ledger bindings on the shared canonical adapters."""


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
