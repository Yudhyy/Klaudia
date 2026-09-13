Procedure:
1. Discover and inspect the intended table. Choose the business entity and period from evidence, not from the active workbook alone.
2. Confirm the registered table represents the requested records. Summary footers or multiple kinds of records can invalidate a total even when arithmetic is exact.
3. Use calculate for sums and nonblank counts. Select exact column names from inspection, equality filters and grouping columns. The server supplies registered bounds and observed revisions.
4. Declare unit_column when a currency or unit column is known. Group or filter by that column to keep units separate. Without unit evidence, report the result as unspecified rather than assigning a currency.
5. Keep numeric_text set to reject unless the source convention explicitly uses plain decimal text without grouping separators. Do not guess the meaning of locale-formatted amounts.
6. If a revision conflict occurs, inspect again. Stale catalogue metadata needs a checked refresh through table-authoring when those tools are enabled. Do not bypass that rejection or estimate a total yourself.
7. Report each value with its metric column, operation, grouping labels, units and source table. Keep blank counts and matched record counts distinct from sums. Never add totals from different currencies without a supported conversion procedure.
8. These are snapshot calculations, not ledger writes or recalculated spreadsheet formulas. State scope limits and do not claim a write occurred. Load financial-execution for bounded records, sorting, lookups, joins, reconciliation, aging and variance. Date-range filters and formula evaluation remain unsupported.
