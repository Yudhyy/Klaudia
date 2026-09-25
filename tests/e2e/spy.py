"""Capture ledger MCP reads and extraction events in application tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


class MCPSpy:
    """Patches `coroutine` on every tool of the given registries to record calls."""

    def __init__(self, registries: list[Any]) -> None:
        self._registries = [r for r in registries if r is not None]
        self._calls: list[tuple[str, dict]] = []

    @property
    def calls(self) -> list[tuple[str, dict]]:
        return self._calls

    @contextmanager
    def capture(self) -> Iterator[list[tuple[str, dict]]]:
        """Record all tool invocations made within the block.

        Resets the call log on entry so each turn captures only its own calls.
        Tools whose `coroutine` cannot be wrapped are skipped (the granular
        checks degrade to "skipped" rather than crashing the run).
        """
        self._calls = []
        originals: list[tuple[Any, Any]] = []

        for reg in self._registries:
            for tool in getattr(reg, "tools", []) or []:
                original = getattr(tool, "coroutine", None)
                if original is None:
                    continue

                def make_wrapper(name: str, original):
                    async def wrapper(**kwargs: Any):
                        self._calls.append((name, dict(kwargs)))
                        return await original(**kwargs)

                    return wrapper

                try:
                    tool.coroutine = make_wrapper(tool.name, original)
                    originals.append((tool, original))
                except Exception:
                    # Pydantic blocked the assignment — skip this tool.
                    continue

        try:
            yield self._calls
        finally:
            for tool, original in originals:
                try:
                    tool.coroutine = original
                except Exception:
                    pass


class ExtractionSpy:
    """Records KIE cache hits/misses by wrapping ExtractionAgent.process.

    cache_hits/cache_misses are not in the public response (they live on the
    Langfuse span), so we derive them from the returned ExtractionResult: a page
    with `from_cache=True` is a hit, otherwise a miss — matching the
    extraction_agent.process output schema (cache_hits / cache_misses).
    """

    def __init__(self, extraction_agent) -> None:
        self._agent = extraction_agent
        self._records: list[dict] = []

    @property
    def records(self) -> list[dict]:
        return self._records

    @contextmanager
    def capture(self) -> Iterator[list[dict]]:
        self._records = []
        original = self._agent.process

        async def wrapper(attachment, session_id, user_id):
            result = await original(attachment, session_id, user_id)
            pages = getattr(result, "pages", []) or []
            hits = sum(1 for p in pages if p.get("from_cache"))
            misses = len(pages) - hits
            self._records.append(
                {
                    "file_name": getattr(result, "file_name", "?"),
                    "pages": len(pages),
                    "cache_hits": hits,
                    "cache_misses": misses,
                    "status": getattr(result, "status", "?"),
                    "summary": getattr(result, "summary", ""),
                }
            )
            return result

        self._agent.process = wrapper  # plain class, attribute assignment is fine
        try:
            yield self._records
        finally:
            self._agent.process = original
