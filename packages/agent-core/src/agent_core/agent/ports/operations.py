"""Agent operations supplied by the current standalone or durable execution scope."""

from typing import Any, Protocol

from agent_core.agent.ports.append import TableAppendProposal
from agent_core.agent.ports.authoring import AuthoringProposal
from agent_core.agent.ports.typed import TypedEditProposal


class OperationExecutor(Protocol):
    """Application boundary enforcing current ownership for proposals and execution."""

    async def prepare(
        self, user_id: int, request: TableAppendProposal
    ) -> dict[str, str]:
        """Persist checked records under authenticated ownership.

        Args:
            user_id: Server-authenticated identity.
            request: Records and inspected revisions before key assignment.

        Returns:
            Stored proposal reference without claiming a committed write.
        """
        ...

    async def prepare_authoring(
        self, user_id: int, request: AuthoringProposal
    ) -> dict[str, Any]:
        """Persist an owned table-authoring proposal before execution.

        Args:
            user_id: Server-authenticated task owner.
            request: Exact authoring action and observed revisions.

        Returns:
            Original stored reference with optional approval identity.
        """
        ...

    async def inspect_typed(self, user_id: int, workbook_id: str) -> dict[str, Any]:
        """Read typed cells from one owned snapshot.

        Args:
            user_id: Authenticated owner.
            workbook_id: Selected workbook.

        Returns:
            Bounded typed evidence and source fingerprint.
        """
        ...

    async def prepare_typed(
        self, user_id: int, request: TypedEditProposal
    ) -> dict[str, Any]:
        """Store checked typed edits without changing financial cells.

        Args:
            user_id: Authenticated owner.
            request: Exact batch and observed fingerprint.

        Returns:
            Original proposal reference.
        """
        ...

    async def execute(self, user_id: int, operation_ref: str) -> dict[str, Any]:
        """Execute the exact stored request under current ownership.

        Args:
            user_id: Server-authenticated identity.
            operation_ref: Stored proposal reference.

        Returns:
            Committed operation receipt, including exact replay.
        """
        ...
