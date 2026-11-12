"""Map only the ledger failures covered by the agent capability contract."""

from collections.abc import Iterator
from contextlib import contextmanager

from ledger_core.errors import (
    ApprovalRequiredError as LedgerApprovalRequired,
)
from ledger_core.errors import (
    RevisionConflictError as LedgerRevisionConflict,
)
from ledger_core.resources import (
    ResourceExistsError as LedgerResourceExists,
)
from ledger_core.resources import (
    ResourceNotFoundError as LedgerResourceNotFound,
)

from agent_core.agent.ports.errors import (
    ApprovalRequiredError,
    ResourceExistsError,
    ResourceNotFoundError,
    RevisionConflictError,
)


@contextmanager
def ledger_failures() -> Iterator[None]:
    """Retain expected failure text, approval payloads and exception causes.

    Yields:
        Control to canonical validation and the selected backend capability.

    Raises:
        ResourceNotFoundError: A source or operation is missing or foreign.
        ResourceExistsError: A table overlaps the requested region.
        RevisionConflictError: A source, catalogue or policy changed.
        ApprovalRequiredError: The stored operation requires a human decision.
    """
    try:
        yield
    except LedgerResourceNotFound as failure:
        raise ResourceNotFoundError(str(failure)) from failure
    except LedgerResourceExists as failure:
        raise ResourceExistsError(str(failure)) from failure
    except LedgerRevisionConflict as failure:
        raise RevisionConflictError(str(failure)) from failure
    except LedgerApprovalRequired as failure:
        raise ApprovalRequiredError(failure.approval) from failure
