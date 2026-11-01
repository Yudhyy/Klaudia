"""Agent-owned tool binding for backend policy reconciliation."""

from typing import Any

from langchain_core.tools import StructuredTool

from agent_core.agent.ports.reconciliation import (
    PolicyReconciler,
    PolicyReconciliation,
)


class PolicyReconciliationTools:
    """Bind model intent without interpreting policy or financial evidence."""

    def __init__(self, reconciler: PolicyReconciler) -> None:
        """Build the tool over a backend bound to the authenticated owner.

        Args:
            reconciler: Backend that checks saved policy and exact ledger reads.
        """
        self._reconciler = reconciler
        self.tool = StructuredTool.from_function(
            coroutine=self._reconcile,
            name="reconcile_with_policy",
            description="Reconcile all rows in two owned registered tables under saved structured accounting policy. Read /accounting-policy.md first and supply its revision. Requires explicit unit columns and matching table entities. as_of selects policy validity, not a row date filter. Jurisdiction is caller-declared. Read-only; no legal compliance certification.",
            args_schema=PolicyReconciliation,
        )

    async def _reconcile(self, **arguments: Any) -> dict[str, Any]:
        """Convert tool arguments to the consumer request without adding rules.

        Args:
            arguments: Source columns, declared scope and observed policy revision.

        Returns:
            Backend evidence without changes.
        """
        return await self._reconciler.reconcile(
            PolicyReconciliation.model_validate(arguments)
        )
