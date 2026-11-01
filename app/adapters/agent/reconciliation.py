"""Map policy and ledger failures at the agent capability boundary."""

from typing import Any

from app.adapters.agent.errors import ledger_failures
from app.services.memory.reconciliation import PolicyReconciler
from agent_core.agent.ports.reconciliation import PolicyReconciliation


class ReconciliationAdapter:
    """Keep saved-policy checks and exact calculations in the backend."""

    def __init__(self, reconciler: PolicyReconciler) -> None:
        """Bind the owner-scoped backend reconciler.

        Args:
            reconciler: Backend with policy, owner and revision checks.
        """
        self._reconciler = reconciler

    async def reconcile(self, request: PolicyReconciliation) -> dict[str, Any]:
        """Read unchanged policy evidence with consumer-owned expected failures.

        Args:
            request: Source intent and saved policy revision.

        Returns:
            Exact financial evidence with checked policy scope.
        """
        with ledger_failures():
            return await self._reconciler.reconcile(request)
