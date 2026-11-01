"""Build current runtimes for checks against fixed release fixtures."""

import json
from itertools import product
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.services.core.archive_tools import ArchiveReader
from app.services.memory.reconciliation import PolicyReconciler
from app.services.memory.tools import MemoryReader
from klaudia.core.agent.context import TaskContext
from klaudia.core.agent.documents import DocumentTools
from klaudia.core.agent.memory import MemoryTools
from klaudia.core.agent.reconciliation import PolicyReconciliationTools

FIXTURES = Path(__file__).parent / "fixtures" / "agent_compatibility" / "v1"
CAPABILITIES = ("operations", "authoring", "archive", "memory", "reconciliation")
PROFILES = {
    "+".join(name for name, enabled in zip(CAPABILITIES, flags) if enabled)
    or "read_only": dict(zip(CAPABILITIES, flags))
    for flags in product((False, True), repeat=len(CAPABILITIES))
}


def read_fixture(name: str) -> Any:
    """Read a saved JSON contract without calling current contract code.

    Args:
        name: File name relative to the fixed release directory.

    Returns:
        Decoded fixture content.
    """
    return json.loads((FIXTURES / name).read_bytes())


def agent_options(
    capabilities: dict[str, bool], context: TaskContext
) -> dict[str, Any]:
    """Bind optional tools to test stores and the supplied server identity.

    Args:
        capabilities: Enabled constructor inputs for this profile.
        context: Authenticated task context.

    Returns:
        Optional MainAgent constructor arguments.
    """
    return {
        "operations": AsyncMock() if capabilities["operations"] else None,
        "authoring": AsyncMock() if capabilities["authoring"] else None,
        "archive_tools": DocumentTools(
            ArchiveReader(AsyncMock(), context.user_id)
        ).tools
        if capabilities["archive"]
        else (),
        "memory_tool": MemoryTools(MemoryReader(AsyncMock(), context.user_id)).read_tool
        if capabilities["memory"]
        else None,
        "reconciliation_tool": PolicyReconciliationTools(
            PolicyReconciler(AsyncMock(), AsyncMock(), context.user_id)
        ).tool
        if capabilities["reconciliation"]
        else None,
    }


def tool_contracts(tools: list[StructuredTool]) -> dict[str, Any]:
    """Read full argument schemas and the model-facing function definitions.

    Args:
        tools: Tools bound by the current runtime.

    Returns:
        Complete contracts indexed by tool name, in binding order.
    """
    return {
        tool.name: {
            "description": tool.description,
            "args_schema": tool.args_schema.model_json_schema(),
            "model_schema": convert_to_openai_tool(tool),
        }
        for tool in tools
    }
