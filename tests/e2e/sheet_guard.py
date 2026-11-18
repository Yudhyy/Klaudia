# TODO: Migrate legacy e2e sheet fixture to research benchmarks (SpreadsheetBench 2, MBABench).

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tests.e2e.ledger_seeder import (
    LedgerSeeder,
    iter_json_objects as _iter_json_objects,
    tool as _tool,
    titles as _titles,
)

logger = logging.getLogger(__name__)

# Sheets that mutating cases write to and that later cases assert amounts on.
GUARDED_SHEETS = ("Jun", "Rangkuman Total")

_TABLE_MD = Path(__file__).resolve().parent / "fixtures" / "TABLE.md"


def load_table_md(
    path: Path = _TABLE_MD,
) -> tuple[list[str], dict[str, list[list[str]]]]:
    """Parse docs/TABLE.md into (ordered sheet names, {name: rows}).

    Format per block: 'Index: N,' / 'Name: <name>,' / 'Table:' then tab-
    separated rows until a blank line. Name may be quoted and/or comma-suffixed.
    """
    names: list[str] = []
    data: dict[str, list[list[str]]] = {}
    current: str | None = None
    in_table = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("Index:"):
            current, in_table = None, False
            continue
        if line.startswith("Name:"):
            name = line[len("Name:") :].strip().strip(",").strip().strip('"').strip()
            current, in_table = name, False
            names.append(name)
            data[name] = []
            continue
        if line.strip() == "Table:":
            in_table = True
            continue
        if not line.strip():
            in_table = False
            continue
        if in_table and current is not None:
            data[current].append(line.split("\t"))

    return names, data


def _norm_cell(value: Any) -> str:
    """Comparable form of one cell.

    Amounts round-trip in different shapes depending on backend and locale
    ("2.164.500" written, 2164500 read back), so digits are compared
    separator-free. Anything non-numeric compares case-insensitively.
    """
    text = "" if value is None else str(value).strip()
    digits = text.replace(".", "").replace(",", "").replace(" ", "")
    if digits.isdigit():
        return digits.lstrip("0") or "0"
    return text.lower()


def _norm_grid(grid: list[list[Any]] | None) -> tuple:
    """Comparable form of a grid, ignoring trailing empty cells and rows."""
    rows: list[tuple] = []
    for row in grid or []:
        cells = [_norm_cell(c) for c in row]
        while cells and cells[-1] == "":
            cells.pop()
        rows.append(tuple(cells))
    while rows and not any(rows[-1]):
        rows.pop()
    return tuple(rows)


class SheetGuard:
    """Restore guarded sheets + drop stray tabs, from the TABLE.md template.

    spreadsheet_id scopes every tool call to the test user's spreadsheet
    (ledger backend, per-user tenancy). None = the backend's default
    workspace (gsheets, or ledger before tenancy resolution).
    """

    def __init__(
        self, gsheets_registry: Any, spreadsheet_id: str | None = None
    ) -> None:
        self._reg = gsheets_registry
        self._spreadsheet_id = spreadsheet_id
        self._seeder = LedgerSeeder(gsheets_registry, spreadsheet_id)
        self._names: list[str] = []
        self._data: dict[str, list[list[str]]] = {}

    def _args(self, **kwargs: Any) -> dict[str, Any]:
        if self._spreadsheet_id is not None:
            kwargs["spreadsheet_id"] = self._spreadsheet_id
        return kwargs

    @property
    def _baseline_titles(self) -> set[str]:
        return set(self._names)

    async def snapshot(self) -> None:
        """Load the TABLE.md baseline. Named snapshot() for fixture symmetry;
        the baseline is the file, not the live sheet, so there is nothing to
        read from Sheets here."""
        try:
            self._names, self._data = load_table_md()
            logger.info("SheetGuard: loaded TABLE.md baseline tabs=%s", self._names)
        except Exception as exc:
            logger.warning("SheetGuard: failed to load TABLE.md: %s", exc)

    async def _list_titles(self) -> list[str]:
        lister = _tool(self._reg, "tool_list_sheets")
        if lister is None:
            return []
        try:
            return _titles(await lister.ainvoke(self._args()))
        except Exception as exc:
            logger.warning("SheetGuard: list_sheets failed: %s", exc)
            return []

    async def seed(self) -> None:
        """Materialize the FULL TABLE.md baseline (all tabs, exact contents).

        Only used for spreadsheet-scoped runs (ledger tenancy): the test
        user's spreadsheet is provisioned empty, unlike the gsheets fixture
        sheet which is assumed to match TABLE.md already.
        """
        if not self._names:
            logger.warning("SheetGuard: no baseline loaded; seed skipped")
            return
        existing = set(await self._list_titles())
        for name in self._names:
            await self._write_sheet(name, create=name not in existing)
        await self._drop_extra_sheets()

    async def restore(self) -> None:
        """Repair every baseline tab that drifted from TABLE.md.

        Deliberately not limited to GUARDED_SHEETS: a destructive case can
        empty ANY tab (H03 asked to "delete all sheet data" cleared all
        seven), and rewriting only Jun + Rangkuman Total left the rest
        empty for every later case — which is how MT02/R04/R05 ended up
        asserting against blank sheets. Reads first and rewrites only what
        actually differs, so the common no-drift path stays cheap.
        """
        # 1. Drop any tab TABLE.md does not define (copy/new/rename leftovers).
        await self._drop_extra_sheets()

        # 2. Repair missing or drifted baseline tabs.
        existing = set(await self._list_titles())
        current = await self._read_grids([n for n in self._names if n in existing])
        for name in self._names:
            if name not in existing:
                await self._write_sheet(name, create=True)
                continue
            if _norm_grid(current.get(name)) != _norm_grid(self._data.get(name)):
                logger.info("SheetGuard: repairing drifted sheet %r", name)
                await self._write_sheet(name, create=False)

    async def _read_grids(self, titles: list[str]) -> dict[str, list[list[Any]]]:
        """Read several tabs at once, falling back to per-sheet reads."""
        if not titles:
            return {}
        grids: dict[str, list[list[Any]]] = {}
        multi = _tool(self._reg, "tool_get_multiple_sheet_data")
        if multi is not None:
            try:
                raw = await multi.ainvoke(
                    {"queries": [self._args(sheet=t) for t in titles]}
                )
                for obj in _iter_json_objects(raw):
                    title = obj.get("sheet")
                    if title is not None and "data" in obj:
                        grids[title] = obj["data"] or []
                if grids:
                    return grids
            except Exception as exc:
                logger.warning("SheetGuard: multi-read failed: %s", exc)

        reader = _tool(self._reg, "tool_get_sheet_data")
        if reader is None:
            return grids
        for title in titles:
            try:
                raw = await reader.ainvoke(self._args(sheet=title))
                obj = next(_iter_json_objects(raw), {})
                grids[title] = obj.get("values") or []
            except Exception as exc:
                logger.warning("SheetGuard: read of %r failed: %s", title, exc)
        return grids

    async def _write_sheet(self, name: str, create: bool) -> None:
        """Create (or clear) one tab, then write its TABLE.md rows."""
        await self._seeder.write_grid(name, self._data.get(name) or [], create=create)

    async def _drop_extra_sheets(self) -> None:
        baseline = self._baseline_titles
        if not baseline:
            return  # never parsed a baseline — refuse to delete blindly
        delete = _tool(self._reg, "tool_delete_sheet")
        if delete is None:
            return
        for title in await self._list_titles():
            if title in baseline:
                continue
            try:
                await delete.ainvoke(self._args(sheet=title))
                logger.info("SheetGuard: deleted non-baseline sheet %r", title)
            except Exception as exc:
                logger.warning("SheetGuard: delete of %r failed: %s", title, exc)
