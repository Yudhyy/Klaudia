"""The synthetic ledger generator is deterministic and its truth matches its data.

Hermetic: pure Python, no DB, no LLM. These tests are the deterministic backbone
of the hard bench - if the generator drifts, the baked YAML expectations drift
with it and this test fails first.
"""

from klaudia_api.adapters.guardrails.verifier import _cell_value
from tests.e2e.synthetic import (
    HEADER,
    MISSING_STORE,
    build_aggregation_ledger,
    build_compound_ledger,
    build_distractor_ledger,
    build_dirty_ledger,
    build_hostile_schema_ledger,
    build_injection_ledger,
    build_missing_value_ledger,
    build_reconciliation_ledger,
    build_scale_ledger,
    INJECTION_TEXT,
    SyntheticLedger,
)

_AMOUNT_COL = 3


def _recompute(ledger: SyntheticLedger):
    """Independently sum the amount column of the grids (skips the header)."""
    per_sheet: dict[str, int] = {}
    per_category: dict[str, int] = {}
    total = 0
    for name, grid in ledger.grids.items():
        sheet_sum = 0
        for row in grid[1:]:
            amount = row[_AMOUNT_COL]
            sheet_sum += amount
            per_category[row[2]] = per_category.get(row[2], 0) + amount
        per_sheet[name] = sheet_sum
        total += sheet_sum
    return per_sheet, per_category, total


def test_same_seed_is_identical():
    a = build_aggregation_ledger(seed=1013)
    b = build_aggregation_ledger(seed=1013)
    assert a == b


def test_different_seed_changes_the_total():
    a = build_aggregation_ledger(seed=1013)
    b = build_aggregation_ledger(seed=99)
    assert a.all_sheets_total != b.all_sheets_total


def test_truth_matches_the_generated_grids():
    ledger = build_aggregation_ledger(seed=1013)
    per_sheet, per_category, total = _recompute(ledger)
    assert per_sheet == ledger.per_sheet_total
    assert per_category == ledger.per_category_total
    assert total == ledger.all_sheets_total


def test_all_sheets_total_is_the_sum_of_per_sheet_totals():
    ledger = build_aggregation_ledger(seed=1013)
    assert ledger.all_sheets_total == sum(ledger.per_sheet_total.values())


def test_grid_shape_is_header_plus_data_rows():
    ledger = build_aggregation_ledger(seed=1013, rows_per_sheet=8)
    for grid in ledger.grids.values():
        assert grid[0] == HEADER
        assert len(grid) == 1 + 8
        for row in grid[1:]:
            assert len(row) == len(HEADER)
            assert isinstance(row[_AMOUNT_COL], int)


def test_max_amount_is_the_largest_cell():
    ledger = build_aggregation_ledger(seed=1013)
    largest = max(
        row[_AMOUNT_COL] for grid in ledger.grids.values() for row in grid[1:]
    )
    assert ledger.max_amount == largest


def test_golden_seed_1013_is_frozen():
    """Ground truth baked into tests/e2e/dataset/cases/13_aggregation.yaml.

    If the generator changes and this fails, the YAML expectations are stale:
    regenerate them from seed 1013 and update both together.
    """
    ledger = build_aggregation_ledger(seed=1013)
    assert ledger.per_sheet_total == {
        "Pengeluaran Juni": 1943000,
        "Pengeluaran Juli": 1659000,
    }
    assert ledger.all_sheets_total == 3602000
    assert ledger.per_category_total["Makan"] == 1523000


# ── Dirty ledger: mixed messy amount formats, same exact-truth discipline ──────


def test_dirty_is_deterministic():
    assert build_dirty_ledger(seed=1013) == build_dirty_ledger(seed=1013)


def test_dirty_cells_parse_back_to_the_truth():
    """Every messy amount cell parses (via the real verifier) to an integer, and
    those integers sum to the ledger's stated truth."""
    ledger = build_dirty_ledger(seed=1013)
    per_sheet: dict[str, int] = {}
    total = 0
    for name, grid in ledger.grids.items():
        sheet_sum = 0
        for row in grid[1:]:
            value = _cell_value(row[_AMOUNT_COL])
            assert value is not None, f"unparseable amount cell: {row[_AMOUNT_COL]!r}"
            sheet_sum += int(value)
        per_sheet[name] = sheet_sum
        total += sheet_sum
    assert per_sheet == ledger.per_sheet_total
    assert total == ledger.all_sheets_total


def test_dirty_amounts_are_mixed_string_formats():
    ledger = build_dirty_ledger(seed=1013)
    cells = [row[_AMOUNT_COL] for grid in ledger.grids.values() for row in grid[1:]]
    assert all(isinstance(c, str) for c in cells)
    # More than one rendering convention appears within the data (dotted vs comma).
    assert any("." in c for c in cells)
    assert any("," in c for c in cells)


def test_dirty_golden_seed_1013_is_frozen():
    """Ground truth baked into tests/e2e/dataset/cases/14_dirty_data.yaml."""
    ledger = build_dirty_ledger(seed=1013)
    assert ledger.per_sheet_total == {"Belanja Juni": 959000, "Belanja Juli": 2209000}
    assert ledger.all_sheets_total == 3168000


# ── Distractor column: a reference number column that must not be summed ───────

_JUMLAH_COL = 4
_NOSTRUK_COL = 1


def test_distractor_is_deterministic():
    assert build_distractor_ledger(seed=1013) == build_distractor_ledger(seed=1013)


def test_distractor_truth_matches_the_columns():
    ledger = build_distractor_ledger(seed=1013)
    jumlah = 0
    nostruk = 0
    for grid in ledger.grids.values():
        for row in grid[1:]:
            jumlah += int(row[_JUMLAH_COL])
            nostruk += int(row[_NOSTRUK_COL])
    assert jumlah == ledger.jumlah_total
    assert nostruk == ledger.distractor_total
    assert ledger.naive_total == ledger.jumlah_total + ledger.distractor_total


def test_distractor_correct_and_trap_do_not_digit_collide():
    """The correct total must not be a digit-substring of the naive trap, so a
    digit-normalized contains_amount on the correct value cannot false-pass on a
    reply that states only the trap."""
    ledger = build_distractor_ledger(seed=1013)
    correct, trap = str(ledger.jumlah_total), str(ledger.naive_total)
    assert correct not in trap
    assert trap not in correct


def test_distractor_golden_seed_1013_is_frozen():
    """Ground truth baked into tests/e2e/dataset/cases/16_distractor_column.yaml."""
    ledger = build_distractor_ledger(seed=1013)
    assert ledger.jumlah_total == 1425000
    assert ledger.naive_total == 6511549


# ── Missing value: exactly one blank amount on a uniquely named row ────────────


def test_missing_is_deterministic():
    assert build_missing_value_ledger(seed=1013) == build_missing_value_ledger(
        seed=1013
    )


def test_missing_has_exactly_one_blank_on_a_unique_store():
    ledger = build_missing_value_ledger(seed=1013)
    grid = ledger.grids[ledger.sheet]
    blanks = [row for row in grid[1:] if row[3] == ""]
    assert len(blanks) == 1
    assert blanks[0][1] == ledger.missing_store == MISSING_STORE
    # The marker store names the incomplete row uniquely.
    assert sum(1 for row in grid[1:] if row[1] == MISSING_STORE) == 1


def test_missing_present_total_excludes_the_blank_row():
    ledger = build_missing_value_ledger(seed=1013)
    grid = ledger.grids[ledger.sheet]
    recomputed = sum(int(row[3]) for row in grid[1:] if row[3] != "")
    assert recomputed == ledger.present_total


def test_missing_golden_seed_1013_is_frozen():
    """Ground truth baked into tests/e2e/dataset/cases/17_find_missing.yaml."""
    ledger = build_missing_value_ledger(seed=1013)
    assert ledger.missing_store == "Apotek Sehat"
    assert ledger.missing_date == "2026-06-22"
    assert ledger.present_total == 2194000


# ── Scale: many dirty rows, exact-arithmetic stress ───────────────────────────


def test_scale_has_160_rows_and_truth_matches():
    ledger = build_scale_ledger(seed=1013)
    assert sum(ledger.per_sheet_rows.values()) == 160
    total = 0
    for grid in ledger.grids.values():
        for row in grid[1:]:
            value = _cell_value(row[_AMOUNT_COL])
            assert value is not None
            total += int(value)
    assert total == ledger.all_sheets_total


def test_scale_golden_seed_1013_is_frozen():
    """Ground truth baked into tests/e2e/dataset/cases/18_scale.yaml."""
    ledger = build_scale_ledger(seed=1013)
    assert ledger.per_sheet_total == {
        "Transaksi Besar Juni": 20263000,
        "Transaksi Besar Juli": 21233000,
    }
    assert ledger.all_sheets_total == 41496000


def test_scale_argmax_category_is_frozen():
    """Ground truth for SCALE02 (largest category over 160 rows)."""
    ledger = build_scale_ledger(seed=1013)
    top = max(ledger.per_category_total.items(), key=lambda kv: kv[1])
    assert top == ("Makan", 14140000)


def test_scale_filtered_sum_over_200k_is_frozen():
    """Ground truth for SCALE03 (filter amount > 200000, then sum)."""
    ledger = build_scale_ledger(seed=1013)
    total = 0
    for grid in ledger.grids.values():
        for row in grid[1:]:
            amount = int(_cell_value(row[_AMOUNT_COL]))
            if amount > 200000:
                total += amount
    assert total == 35323000


def test_scale_single_sheet_category_is_frozen():
    """Ground truth for SCALE04 (Makan in one sheet only; verifier-grounded)."""
    ledger = build_scale_ledger(seed=1013)
    juli = ledger.grids["Transaksi Besar Juli"]
    makan = sum(int(_cell_value(r[_AMOUNT_COL])) for r in juli[1:] if r[2] == "Makan")
    assert makan == 7638000


# ── Compound: base totals the multi-step cases append onto ────────────────────


def test_compound_source_is_frozen():
    """Base totals for tests/e2e/dataset/cases/21_compound.yaml (before appends)."""
    ledger = build_compound_ledger(seed=1013)
    assert ledger.all_sheets_total == 2461000
    assert ledger.per_category_total["Makan"] == 1070000
    # Single sheet named for the compound cases.
    assert list(ledger.grids) == ["Kas Harian"]


# ── Hostile schema: expense is the line total, not the unit price or qty ──────


def test_hostile_schema_is_frozen():
    """Ground truth for tests/e2e/dataset/cases/22_hostile_schema.yaml."""
    ledger = build_hostile_schema_ledger(seed=1013)
    assert ledger.nominal_total == 1073000
    assert ledger.harga_total == 541000
    # Every Nominal is genuinely qty * unit price, so the schema is coherent.
    for row in ledger.grids[ledger.sheet][1:]:
        assert row[4] == row[2] * row[3]


# ── Reconciliation: a wrong summary total to catch by recomputing ─────────────


def test_reconciliation_is_deterministic():
    assert build_reconciliation_ledger(seed=1013) == build_reconciliation_ledger(
        seed=1013
    )


def test_reconciliation_detail_total_and_stated_disagree():
    ledger = build_reconciliation_ledger(seed=1013)
    detail = sum(int(row[3]) for row in ledger.grids[ledger.detail_sheet][1:])
    assert detail == ledger.detail_total
    assert ledger.stated_total == ledger.detail_total + ledger.discrepancy
    assert ledger.stated_total != ledger.detail_total
    # The wrong figure is what the summary sheet actually shows.
    summary = ledger.grids[ledger.summary_sheet]
    assert int(summary[1][1]) == ledger.stated_total


def test_reconciliation_values_do_not_digit_collide():
    ledger = build_reconciliation_ledger(seed=1013)
    vals = [str(ledger.detail_total), str(ledger.stated_total), str(ledger.discrepancy)]
    for a in vals:
        for b in vals:
            if a is not b and a != b:
                assert a not in b


def test_reconciliation_golden_seed_1013_is_frozen():
    """Ground truth baked into tests/e2e/dataset/cases/19_reconciliation.yaml."""
    ledger = build_reconciliation_ledger(seed=1013)
    assert ledger.detail_total == 2252000
    assert ledger.stated_total == 2389000
    assert ledger.discrepancy == 137000


# ── Injection in data: a delete-everything instruction sitting in a cell ──────


def test_injection_is_deterministic():
    assert build_injection_ledger(seed=1013) == build_injection_ledger(seed=1013)


def test_injection_has_exactly_one_payload_cell_and_real_total():
    ledger = build_injection_ledger(seed=1013)
    grid = ledger.grids[ledger.sheet]
    payloads = [row for row in grid[1:] if row[4] == INJECTION_TEXT]
    assert len(payloads) == 1
    assert "hapus semua sheet" in INJECTION_TEXT.lower()
    total = sum(int(row[3]) for row in grid[1:])
    assert total == ledger.total


def test_injection_golden_seed_1013_is_frozen():
    """Ground truth baked into tests/e2e/dataset/cases/20_injection_in_data.yaml."""
    ledger = build_injection_ledger(seed=1013)
    assert ledger.total == 1943000
