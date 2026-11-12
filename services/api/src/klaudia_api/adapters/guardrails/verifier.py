"""Deterministic numeric verification of agent replies (anti-hallucination).

Finance rule: never trust LLM arithmetic. Every monetary-scale number in
a reply must exact-match a value grounded in this turn's evidence:

- raw cell values from tool outputs (JSON grids or plain text),
- code-computed aggregates: column sums, row sums, grid totals, the
  cross-grid total, and pairwise sums/absolute differences of those
  aggregates (covers "total Juni", "Mei vs Juni", cross-sheet rollups),
- numbers the user or the extraction context supplied.

Claims are integers >= MONETARY_THRESHOLD with year/date tokens excluded;
counts and row indices fall below the threshold by construction. The
grounded set is built without a threshold — more evidence only reduces
false flags.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

MONETARY_THRESHOLD = 1000

_YEAR_RANGE = range(1900, 2101)

# Order matters: separator-grouped forms must win over plain digit runs.
# Indonesian dotted thousands ("2.164.500", comma decimals) and English
# comma thousands ("13,300", dot decimals — the KIE extraction format)
# are both accepted. The lookarounds only reject digit/separator
# neighbors so "Rp2.164.500" and sentence-final "...500." both match.
_NUMBER_RE = re.compile(
    r"(?<![\d.,])"
    r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
    r"(?!\d|[.,]\d)"
)
# Masked before extraction so date components never look like amounts.
_DATE_RES = (
    re.compile(r"\d{4}-\d{2}-\d{2}"),  # 2026-07-01
    re.compile(r"\d{1,2}/\d{1,2}/\d{4}"),  # 30/06/2026
)

# Caps keeping the pairwise closures cheap on pathological turns.
_MAX_PAIRWISE_BASE = 64
_MAX_TEXT_AMOUNTS = 16

ToolRecord = tuple[str, dict[str, Any], str]


@dataclass
class VerificationResult:
    passed: bool
    ungrounded: list[int] = field(default_factory=list)
    claims: set[int] = field(default_factory=set)


def _parse_token(token: str) -> int | None:
    """Normalize one matched number token to an integer value.

    Both locales are accepted: Indonesian "1.234,56" (dot thousands,
    comma decimal) and English "1,234.56". A single-separator token is a
    thousands grouping when every group after the first has exactly 3
    digits ("13,300" -> 13300), else a decimal ("1,5" -> 2). Non-integral
    values round to the nearest int, matching how IDR amounts are stated.
    """
    has_dot, has_comma = "." in token, "," in token
    try:
        if has_dot and has_comma:
            if token.rfind(".") > token.rfind(","):
                normalized = token.replace(",", "")  # 1,234.56
            else:
                normalized = token.replace(".", "").replace(",", ".")  # 1.234,56
            return int(round(float(normalized)))
        if has_dot or has_comma:
            sep = "." if has_dot else ","
            head, *groups = token.split(sep)
            if groups and all(len(g) == 3 for g in groups) and len(head) <= 3:
                return int(head + "".join(groups))
            return int(round(float(token.replace(",", "."))))
        return int(token)
    except ValueError:
        return None


def _extract_numbers(text: str) -> Iterable[tuple[int, bool]]:
    """Yield (value, had_thousands_separator) for every number token."""
    masked = text
    for date_re in _DATE_RES:
        masked = date_re.sub(" ", masked)
    for match in _NUMBER_RE.finditer(masked):
        token = match.group(1)
        value = _parse_token(token)
        if value is not None:
            yield value, ("." in token or "," in token)


def extract_claims(text: str) -> set[int]:
    """Monetary-scale claims in a reply: >= threshold, years excluded."""
    claims: set[int] = set()
    for value, dotted in _extract_numbers(text):
        if value < MONETARY_THRESHOLD:
            continue
        if not dotted and value in _YEAR_RANGE:
            continue
        claims.add(value)
    return claims


def _cell_value(cell: Any) -> float | None:
    """Numeric value of one grid cell, accepting Indonesian string formats."""
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    if isinstance(cell, str):
        stripped = cell.strip().removeprefix("Rp").strip().lstrip("-")
        match = _NUMBER_RE.fullmatch(stripped)
        if match:
            value = _parse_token(match.group(1))
            return None if value is None else float(value)
    return None


def _iter_json_objects(raw: str) -> Iterable[Any]:
    """Yield parsed JSON documents from one string.

    Handles a single object/array as well as whitespace-concatenated
    objects (FastMCP list results arrive as one text block per element).
    """
    s = raw.strip()
    if not s:
        return
    try:
        yield json.loads(s)
        return
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(s):
        chunk = s[pos:].lstrip()
        if not chunk:
            break
        pos += len(s[pos:]) - len(chunk)
        try:
            obj, end = decoder.raw_decode(chunk)
        except json.JSONDecodeError:
            return
        yield obj
        pos += end


def _find_grids(node: Any) -> Iterable[list[list[Any]]]:
    """Recursively locate 2D arrays (grids) inside a parsed JSON document."""
    if isinstance(node, list):
        if node and all(isinstance(row, list) for row in node):
            yield node
        else:
            for item in node:
                yield from _find_grids(item)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _find_grids(value)


def _mean_variants(total: float, count: int) -> set[int]:
    """Integer readings of an average: round, floor, and ceil."""
    if count <= 0:
        return set()
    mean = total / count
    return {int(round(mean)), int(mean // 1), -int(-mean // 1)}


def _grid_aggregates(grid: list[list[Any]]) -> tuple[set[int], list[int], int]:
    """Return (cells + derived values, column sums, grid total).

    Derived values folded into the first set: row sums, column means
    (all integer-rounding variants — "rata-rata pengeluaran"), and
    group-by sums (each text column as key, each numeric column summed
    per group — "total belanja di Indomaret" / per payment method).
    """
    cells: set[int] = set()
    col_totals: dict[int, float] = {}
    col_counts: dict[int, int] = {}
    row_sums: set[int] = set()
    group_sums: dict[tuple[int, str, int], float] = {}
    total = 0.0
    for row in grid:
        row_total = 0.0
        row_numeric = 0
        text_keys = [
            (i, cell.strip())
            for i, cell in enumerate(row)
            if isinstance(cell, str) and cell.strip() and _cell_value(cell) is None
        ]
        for col_index, cell in enumerate(row):
            value = _cell_value(cell)
            if value is None:
                continue
            cells.add(int(round(value)))
            col_totals[col_index] = col_totals.get(col_index, 0.0) + value
            col_counts[col_index] = col_counts.get(col_index, 0) + 1
            row_total += value
            row_numeric += 1
            total += value
            for key_col, key in text_keys:
                group = (key_col, key, col_index)
                group_sums[group] = group_sums.get(group, 0.0) + value
        if row_numeric > 1:
            row_sums.add(int(round(row_total)))
    cells |= row_sums
    for col_index, col_total in col_totals.items():
        cells |= _mean_variants(col_total, col_counts[col_index])
    cells.update(int(round(v)) for v in group_sums.values())
    col_sums = [int(round(v)) for v in col_totals.values()]
    return cells, col_sums, int(round(total))


def grounded_values(records: list[ToolRecord], extra_texts: list[str]) -> set[int]:
    """Every value a truthful reply could state, computed in code."""
    grounded: set[int] = set()
    pairwise_base: list[int] = []
    grid_totals: list[int] = []

    for _name, _args, output in records:
        if not isinstance(output, str) or not output:
            continue
        parsed_any = False
        for document in _iter_json_objects(output):
            parsed_any = True
            for grid in _find_grids(document):
                cells, col_sums, total = _grid_aggregates(grid)
                grounded |= cells
                grounded.update(col_sums)
                grounded.add(total)
                pairwise_base.extend(col_sums)
                pairwise_base.append(total)
                grid_totals.append(total)
            # Scalars outside grids (updatedCells, sheetId...) are harmless
            # to ground and avoid flagging echoed metadata.
            grounded.update(_scalar_ints(document))
        if not parsed_any:
            grounded.update(v for v, _ in _extract_numbers(output))

    if grid_totals:
        grounded.add(sum(grid_totals))

    base = pairwise_base[:_MAX_PAIRWISE_BASE]
    for i, a in enumerate(base):
        for b in base[i + 1 :]:
            grounded.add(a + b)
            grounded.add(abs(a - b))

    tool_values = list(grounded)  # snapshot: tool-derived values only

    text_values: set[int] = set()
    for text in extra_texts:
        if text:
            text_values.update(v for v, _ in _extract_numbers(text))
    grounded |= text_values

    # "Old value + user-supplied amount" derivations: a write turn reads
    # the current total (a grounded tool value) and states the new one.
    # Monetary-scale text amounts combine with every tool-grounded value
    # (and with each other — "5.000 + 7.000", or qty-doubled amounts).
    text_amounts = sorted(v for v in text_values if v >= MONETARY_THRESHOLD)[
        :_MAX_TEXT_AMOUNTS
    ]
    combined: set[int] = set()
    for t in text_amounts:
        for s in tool_values:
            combined.add(s + t)
            combined.add(abs(s - t))
    for i, a in enumerate(text_amounts):
        for b in text_amounts[i:]:
            combined.add(a + b)
            combined.add(abs(a - b))
    grounded |= combined

    return grounded


def _scalar_ints(node: Any) -> set[int]:
    out: set[int] = set()
    if isinstance(node, bool):
        return out
    if isinstance(node, (int, float)):
        out.add(int(round(node)))
    elif isinstance(node, str):
        value = _cell_value(node)
        if value is not None:
            out.add(int(round(value)))
    elif isinstance(node, list):
        for item in node:
            if not isinstance(item, list):  # grids already handled
                out |= _scalar_ints(item)
    elif isinstance(node, dict):
        for value in node.values():
            out |= _scalar_ints(value)
    return out


def verify_reply(
    reply: str, records: list[ToolRecord], extra_texts: list[str]
) -> VerificationResult:
    """Check that every monetary claim in the reply is grounded.

    Args:
        reply: The assistant's draft reply.
        records: This turn's (tool name, args, raw output) records.
        extra_texts: User message, extraction contexts, recent history —
            numbers the model may legitimately echo.

    Returns:
        VerificationResult; passed is True when no ungrounded claims.
    """
    claims = extract_claims(reply)
    if not claims:
        return VerificationResult(passed=True, claims=claims)
    grounded = grounded_values(records, extra_texts)
    ungrounded = sorted(claims - grounded)
    return VerificationResult(
        passed=not ungrounded, ungrounded=ungrounded, claims=claims
    )
