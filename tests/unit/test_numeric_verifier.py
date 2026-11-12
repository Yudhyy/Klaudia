"""Deterministic numeric verification: never trust LLM arithmetic.

Every monetary-scale number in Klaudia's reply must exact-match a value
that is grounded in this turn's tool outputs — either a raw cell value,
a code-computed aggregate (column/row/grid sums, cross-grid sums, and
pairwise sums/differences of those), or a number the user/extraction
context supplied. One wrong digit fails the check.
"""

from klaudia_api.adapters.guardrails.verifier import (
    VerificationResult,
    extract_claims,
    grounded_values,
    verify_reply,
)


class TestExtractClaims:
    def test_indonesian_dotted_thousands(self):
        assert extract_claims("Total belanja Juni Rp2.164.500 ya!") == {2164500}

    def test_plain_integers(self):
        assert extract_claims("Harganya 15000 rupiah") == {15000}

    def test_rp_prefix_with_space(self):
        assert extract_claims("Rp 12.000 untuk Indomie") == {12000}

    def test_multiple_amounts(self):
        text = "Jun: 2.164.500, Mei: 1.750.000, selisih 414.500"
        assert extract_claims(text) == {2164500, 1750000, 414500}

    def test_small_numbers_ignored(self):
        # Counts, indices, quantities are below the monetary threshold.
        assert extract_claims("Ada 12 transaksi di baris 5") == set()

    def test_years_ignored(self):
        assert extract_claims("Laporan tahun 2026") == set()

    def test_dates_ignored(self):
        assert extract_claims("Tanggal 2026-07-01 dan 30/06/2026") == set()

    def test_decimal_comma_rounds_to_int_value(self):
        # "2.164.500,00" is the same amount.
        assert extract_claims("Rp2.164.500,00") == {2164500}

    def test_no_numbers(self):
        assert extract_claims("Sudah kucatat ya!") == set()

    def test_english_comma_thousands(self):
        # KIE extraction JSON uses English formats ("13,300").
        assert extract_claims("Total 13,300 dan 460,841,759") == {13300, 460841759}

    def test_english_decimal_with_comma_thousands(self):
        assert extract_claims("Grand total 1,234.56") == {1235}


class TestGroundedValues:
    def test_cells_from_json_grid(self):
        records = [
            (
                "tool_get_sheet_data",
                {"sheet": "Jun"},
                '{"values": [["Date", "Total"], ["2026-06-01", 15000], '
                '["2026-06-02", "23.500"]]}',
            )
        ]
        grounded = grounded_values(records, [])
        assert 15000 in grounded and 23500 in grounded

    def test_column_sum_aggregate(self):
        records = [
            (
                "tool_get_sheet_data",
                {"sheet": "Jun"},
                '{"values": [["Total"], [15000], [23500], [10000]]}',
            )
        ]
        assert 48500 in grounded_values(records, [])

    def test_row_sum_aggregate(self):
        records = [
            (
                "tool_get_sheet_data",
                {"sheet": "Jun"},
                '{"values": [[15000, 23500, 10000]]}',
            )
        ]
        assert 48500 in grounded_values(records, [])

    def test_cross_grid_sum_and_difference(self):
        # "Mei vs Juni" — two reads; their totals' sum and difference
        # must both be grounded.
        records = [
            ("tool_get_sheet_data", {"sheet": "Mei"}, '{"values": [[1750000]]}'),
            ("tool_get_sheet_data", {"sheet": "Jun"}, '{"values": [[2164500]]}'),
        ]
        grounded = grounded_values(records, [])
        assert 3914500 in grounded  # sum
        assert 414500 in grounded  # difference

    def test_indonesian_formatted_cells(self):
        records = [
            (
                "tool_get_sheet_data",
                {"sheet": "Rangkuman Total"},
                '{"values": [["Juni", "2.164.500"]]}',
            )
        ]
        assert 2164500 in grounded_values(records, [])

    def test_column_average_is_grounded(self):
        # "rata-rata pengeluaran bulanan" — all integer-rounding variants.
        records = [
            (
                "tool_get_sheet_data",
                {"sheet": "Rangkuman Total"},
                '{"values": [["Bulan", "Total"], ["Jan", 1000], ["Feb", 2000], '
                '["Mar", 2000]]}',
            )
        ]
        grounded = grounded_values(records, [])
        # sum=5000, n=3 -> floor 1666, round/ceil 1667
        assert 1667 in grounded and 1666 in grounded

    def test_group_by_category_sum_is_grounded(self):
        # "total belanja di Indomaret" — subset sum keyed by a text column.
        records = [
            (
                "tool_get_sheet_data",
                {"sheet": "Jun"},
                '{"values": [["Toko", "Total"], ["Indomaret", 15000], '
                '["Alfamart", 9000], ["Indomaret", 23500]]}',
            )
        ]
        assert 38500 in grounded_values(records, [])

    def test_extra_texts_are_grounded(self):
        # Numbers the user typed (or extraction produced) are legit to echo.
        grounded = grounded_values([], ["tolong catat pengeluaran 55.000"])
        assert 55000 in grounded

    def test_english_formatted_extraction_context_grounds(self):
        # Mock-KIE JSON amounts: "unit_price": "13,300", "discount": "-2,500".
        ctx = '"unit_price": "13,300", "discount_price": "-2,500", "total": "10,800"'
        grounded = grounded_values([], [ctx])
        assert {13300, 2500, 10800} <= grounded

    def test_old_total_plus_user_amount_is_grounded(self):
        # Write flow: agent reads the old total (cell), user supplies the new
        # amount; the reply states old+new — a legitimate derivation.
        records = [
            (
                "tool_get_sheet_data",
                {"sheet": "Rangkuman Total"},
                '{"values": [["Juni", "1.501.900"]]}',
            )
        ]
        grounded = grounded_values(records, ["catat pengeluaran 5.000 dong"])
        assert 1506900 in grounded  # 1.501.900 + 5.000
        assert 1496900 in grounded  # difference reading too

    def test_non_json_output_numbers_still_grounded(self):
        # Coordinate-annotated / plain-text tool outputs.
        records = [("tool_get_sheet_data", {}, "A2: 15.000\nA3: 23.500")]
        grounded = grounded_values(records, [])
        assert 15000 in grounded and 23500 in grounded


class TestVerifyReply:
    _JUN = (
        "tool_get_sheet_data",
        {"sheet": "Jun"},
        '{"values": [["Total"], [15000], [23500], [10000]]}',
    )

    def test_passes_on_grounded_total(self):
        result = verify_reply("Total pengeluaran Juni Rp48.500", [self._JUN], [])
        assert isinstance(result, VerificationResult)
        assert result.passed
        assert result.ungrounded == []

    def test_fails_on_wrong_digit(self):
        # One wrong digit is unacceptable.
        result = verify_reply("Total pengeluaran Juni Rp48.600", [self._JUN], [])
        assert not result.passed
        assert result.ungrounded == [48600]

    def test_passes_when_no_claims(self):
        result = verify_reply("Sudah kucatat ya!", [self._JUN], [])
        assert result.passed

    def test_passes_with_no_tool_records_and_no_claims_source(self):
        # No tools ran and the reply invents an amount -> ungrounded.
        result = verify_reply("Totalmu Rp99.999", [], [])
        assert not result.passed

    def test_user_supplied_amount_passes_without_tools(self):
        result = verify_reply(
            "Oke, kucatat 55.000 ya!", [], ["catat pengeluaran 55.000"]
        )
        assert result.passed
